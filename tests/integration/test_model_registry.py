import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.migrate import migrate
from yta_alpha.model_registry import PREFIX, ModelRegistry, verify_registration
from yta_alpha.train_cli import InferenceInput
from yta_alpha.training import predict, save_artifact

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set YTA_TEST_DATABASE_URL for actual PostgreSQL")
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test")
    migrate(url, ROOT / "infra/migrations")
    config.update(user="yta_app", password="local-yta-app")
    return ModelRegistry(os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL", make_conninfo(**config)))


@pytest.fixture(scope="module")
def trained():
    spec = importlib.util.spec_from_file_location(
        "training_fixture", ROOT / "services/alpha-engine/tests/test_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calendar = TradingCalendar()
    bundle = module.experiment(calendar, 5)
    return module.fit(bundle, calendar), bundle, calendar


@pytest.fixture
def artifact(trained, tmp_path):
    model, _, _ = trained
    # Each scenario has an independent identity so rollback/concurrency assertions are exact.
    model = model.model_copy(
        update={
            "source_hashes": {**model.source_hashes, "test-scenario": content_hash(uuid4().hex)}
        }
    )
    path = tmp_path / "model.json"
    digest = save_artifact(model, path)
    return path, digest, model


def test_concurrent_registration_is_one_version_and_one_audit(registry, artifact):
    path, digest, model = artifact
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: registry.register(path, digest), range(8)))
    assert len(set(identities)) == 1
    identity = identities[0]
    saved = registry.get(identity)
    assert saved.artifact == model
    assert saved.artifact_file_sha256 == digest
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_versions WHERE id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )
        audit = connection.execute(
            "SELECT actor_id,payload FROM yta.audit_events "
            "WHERE action='model.experimental_registered' AND resource_id=%s",
            (identity,),
        ).fetchall()
    assert len(audit) == 1 and audit[0]["actor_id"] is None
    assert audit[0]["payload"]["status"] == "EXPERIMENTAL"


def test_audit_failure_rolls_back_model_registration(registry, artifact, monkeypatch):
    path, digest, _ = artifact
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        registry.register(path, digest)
    monkeypatch.setattr(registry, "_connect", original)
    with original() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_versions "
                "WHERE config->>'artifact_file_sha256'=%s",
                (digest,),
            ).fetchone()["n"]
            == 0
        )
    identity = registry.register(path, digest)
    assert registry.get(identity).artifact_file_sha256 == digest


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_runtime_cannot_mutate_model_facts(registry, artifact, verb):
    identity = registry.register(artifact[0], artifact[1])
    queries = {
        "UPDATE": "UPDATE yta.model_versions SET id=id WHERE id=%s",
        "DELETE": "DELETE FROM yta.model_versions WHERE id=%s",
        "TRUNCATE": "TRUNCATE yta.model_versions",
    }
    with pytest.raises(psycopg.Error):
        with registry._connect() as connection:
            connection.execute(queries[verb], (identity,) if verb != "TRUNCATE" else None)
    assert registry.get(identity).artifact.status == "EXPERIMENTAL"


def test_registration_cli_and_db_inference_survive_source_file_removal(
    registry, artifact, trained, tmp_path
):
    path, digest, model = artifact
    env = {**os.environ, "YTA_MODEL_DATABASE_URL": registry.url}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.train_cli",
            "register",
            "--artifact",
            str(path),
            "--sha256",
            digest,
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    identity = json.loads(result.stdout)["model_id"]
    path.unlink()
    _, bundle, calendar = trained
    inputs = bundle[1][45:50]
    source = tmp_path / "inference.json"
    source.write_text(InferenceInput(snapshots=inputs).model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.train_cli",
            "predict-registered",
            "--input",
            str(source),
            "--model-id",
            identity,
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["predictions"] == list(
        predict(model, inputs, calendar=calendar)
    )
    assert registry.get(identity).artifact == model


def test_bad_artifact_hash_does_not_register(registry, artifact):
    with pytest.raises(DomainError) as error:
        registry.register(artifact[0], "0" * 64)
    assert error.value.code == "MODEL_INTEGRITY_ERROR"


def test_unknown_or_baseline_id_not_exposed_as_trained_model(registry):
    for identity in ("momentum20-v1", PREFIX + "0" * 64, "../model"):
        with pytest.raises(DomainError) as error:
            registry.get(identity)
        assert error.value.code == "MODEL_NOT_FOUND"


def test_restore_verifier_checks_nested_model_hash(registry, artifact):
    identity = registry.register(artifact[0], artifact[1])
    payload = registry.get(identity).model_dump(mode="json")
    payload["artifact"]["model_text"] += "tampered"
    digest = content_hash(payload)
    with pytest.raises(DomainError) as error:
        verify_registration(PREFIX + digest, digest, payload)
    assert error.value.code == "MODEL_INTEGRITY_ERROR"


def test_private_experimental_inference_http(registry, artifact, trained):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    identity = registry.register(artifact[0], artifact[1])
    _, bundle, calendar = trained
    snapshot = bundle[1][45]
    token = "synthetic-internal-model-token-123456789"
    tool_token = "synthetic-distinct-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool_token),
        database_url=SecretStr(registry.url),
    )
    path = f"/internal/v1/experimental-models/{identity}/predict"
    body = {"snapshot": snapshot.model_dump(mode="json"), "horizon_sessions": 5}
    headers = {"Authorization": f"Bearer {token}"}
    with registry._connect() as connection:
        before = connection.execute(
            "SELECT (SELECT count(*) FROM yta.predictions) AS predictions, "
            "(SELECT count(*) FROM yta.prediction_drafts) AS drafts, "
            "(SELECT count(*) FROM yta.audit_events) AS audits"
        ).fetchone()
    with TestClient(create_app(settings, lambda: snapshot.as_of, calendar)) as client:
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path, json=body, headers={"Authorization": f"Bearer {tool_token}"}
            ).status_code
            == 401
        )
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert result["expected_return"] == str(artifact[2].test_predictions[0])
        assert result["status"] == "EXPERIMENTAL" and result["model_kind"] == "TRAINED"
        assert result["probability_up"] is None and "draft_id" not in result
        assert result["horizon_sessions"] == 5 and result["data_mode"] == "REPLAY"
        assert result["target_session"] == calendar.after(snapshot.bars[-1].session, 5).isoformat()
        assert "model_text" not in result and "database_url" not in result
        assert (
            client.post(path, json={**body, "confirmed": True}, headers=headers).status_code == 422
        )
        mismatch = client.post(path, json={**body, "horizon_sessions": 3}, headers=headers)
        assert mismatch.json()["error"]["code"] == "MODEL_HORIZON_MISMATCH"
        for invalid in (True, 5.0, "5", 2):
            assert (
                client.post(
                    path, json={**body, "horizon_sessions": invalid}, headers=headers
                ).status_code
                == 422
            )
        changed = snapshot.model_dump(mode="json")
        changed["data_mode"] = "LIVE_RESEARCH"
        assert (
            client.post(path, json={**body, "snapshot": changed}, headers=headers).json()["error"][
                "code"
            ]
            == "DATA_MODE_MISMATCH"
        )
        absent = path.replace(identity, PREFIX + "0" * 64)
        assert client.post(absent, json=body, headers=headers).status_code == 404
    with registry._connect() as connection:
        after = connection.execute(
            "SELECT (SELECT count(*) FROM yta.predictions) AS predictions, "
            "(SELECT count(*) FROM yta.prediction_drafts) AS drafts, "
            "(SELECT count(*) FROM yta.audit_events) AS audits"
        ).fetchone()
    assert before == after


def test_experimental_http_unavailable_and_future_input(registry, artifact, trained, monkeypatch):
    from datetime import timedelta

    import yta_alpha.training as training
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    identity = registry.register(artifact[0], artifact[1])
    _, bundle, calendar = trained
    snapshot = bundle[1][45]
    token = "synthetic-internal-model-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.url),
    )
    path = f"/internal/v1/experimental-models/{identity}/predict"
    body = {"snapshot": snapshot.model_dump(mode="json"), "horizon_sessions": 5}
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(settings, lambda: snapshot.as_of, calendar)) as client:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EXPERIMENTAL_MODELS_DISABLED"
    settings = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(
        create_app(settings, lambda: snapshot.as_of - timedelta(seconds=1), calendar)
    ) as client:
        assert (
            client.post(path, json=body, headers=headers).json()["error"]["code"]
            == "FUTURE_INFORMATION"
        )

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings, lambda: snapshot.as_of, calendar)) as client:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
        assert "expected_return" not in response.json()


def test_nonreplay_inference_cannot_precede_registered_model(registry, artifact, trained):
    from yta_alpha.experimental_predictions import ExperimentalRequest, experimental_predict

    identity = registry.register(artifact[0], artifact[1])
    _, bundle, calendar = trained
    snapshot = bundle[1][45]
    # Freeze the server clock at a valid EOD input time. The actual database registration
    # is later, so the artifact cannot be treated as available at this historical point.
    with pytest.raises(DomainError) as error:
        experimental_predict(
            registry,
            identity,
            ExperimentalRequest(snapshot=snapshot, horizon_sessions=5),
            calendar=calendar,
            now=snapshot.as_of,
            replay=False,
        )
    assert error.value.code == "MODEL_NOT_AVAILABLE"


@pytest.mark.parametrize("offset,allowed", [(-1, True), (0, True), (1, False)])
def test_model_registration_availability_boundary(registry, artifact, trained, offset, allowed):
    from datetime import timedelta

    from yta_alpha.experimental_predictions import ExperimentalRequest, experimental_predict

    identity = registry.register(artifact[0], artifact[1])
    _, bundle, calendar = trained
    snapshot = bundle[1][45]
    registration, recorded_at = registry.get_with_timestamp(identity)
    with registry._connect() as connection:
        actual = connection.execute(
            "SELECT created_at FROM yta.model_versions WHERE id=%s", (identity,)
        ).fetchone()["created_at"]
    assert recorded_at == actual
    assert registration == registry.get(identity)

    class TimedRegistry:
        def get_with_timestamp(self, requested):
            assert requested == identity
            return registration, snapshot.as_of + timedelta(microseconds=offset)

    body = ExperimentalRequest(snapshot=snapshot, horizon_sessions=5)
    if allowed:
        result = experimental_predict(
            TimedRegistry(), identity, body, calendar=calendar, now=snapshot.as_of, replay=False
        )
        assert result.expected_return == artifact[2].test_predictions[0]
    else:
        with pytest.raises(DomainError) as error:
            experimental_predict(
                TimedRegistry(), identity, body, calendar=calendar, now=snapshot.as_of, replay=False
            )
        assert error.value.code == "MODEL_NOT_AVAILABLE"
    # Explicit REPLAY can reproduce the experiment before its actual local registration.
    replay = experimental_predict(
        registry, identity, body, calendar=calendar, now=snapshot.as_of, replay=True
    )
    assert replay.expected_return == artifact[2].test_predictions[0]


def test_catalog_pages_match_database_without_exposing_artifacts(registry, artifact):
    from yta_alpha.model_catalog import model_page
    from yta_alpha.models import DataMode

    identity = registry.register(artifact[0], artifact[1])
    with registry._connect() as connection:
        expected = connection.execute(
            "SELECT id FROM yta.model_versions WHERE model_kind='TRAINED' "
            "AND config->'artifact'->>'data_mode'='REPLAY' "
            "AND config->'artifact'->>'horizon_sessions'='5' "
            "ORDER BY created_at DESC,id DESC"
        ).fetchall()
    seen = []
    cursor = None
    while True:
        page = model_page(registry, DataMode.REPLAY, horizon=5, limit=2, cursor=cursor)
        assert len(page.items) <= 2
        for item in page.items:
            assert item.qualification == "UNVERIFIED"
            assert item.metric_scope == "RESERVED_WINDOW_NOT_QUALIFIED_FINAL_EVALUATION"
            assert set(item.model_dump()) == {
                "id",
                "status",
                "qualification",
                "data_mode",
                "horizon_sessions",
                "registered_at",
                "training_cutoff",
                "model_sha256",
                "registration_sha256",
                "training_samples",
                "reserved_window_samples",
                "reserved_window_mae",
                "reserved_window_zero_return_mae",
                "metric_scope",
            }
            seen.append(item.id)
        if page.next_cursor is None:
            break
        assert page.next_cursor != cursor
        cursor = page.next_cursor
    assert seen == [row["id"] for row in expected]
    assert identity in seen and len(set(seen)) == len(seen)


def test_catalog_cursor_scope_and_invalid_inputs(registry, artifact):
    from yta_alpha.model_catalog import model_page
    from yta_alpha.models import DataMode

    identity = registry.register(artifact[0], artifact[1])
    for mode, kwargs in [
        (DataMode.LIVE_RESEARCH, {"cursor": identity}),
        (DataMode.REPLAY, {"cursor": identity, "horizon": 3}),
        (DataMode.REPLAY, {"cursor": PREFIX + "0" * 64}),
        (DataMode.REPLAY, {"cursor": "../model"}),
    ]:
        with pytest.raises(DomainError) as error:
            model_page(registry, mode, **kwargs)
        assert error.value.code == "INVALID_CURSOR"
    for kwargs in ({"limit": 0}, {"limit": 51}, {"limit": True}, {"horizon": True}, {"horizon": 2}):
        with pytest.raises(DomainError):
            model_page(registry, DataMode.REPLAY, **kwargs)


def test_catalog_http_auth_flag_and_server_mode(registry, artifact):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    identity = registry.register(artifact[0], artifact[1])
    token = "synthetic-catalog-service-token-123456789"
    tool_token = "synthetic-catalog-tools-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool_token),
        database_url=SecretStr(registry.url),
        data_mode="REPLAY",
    )
    path = "/internal/v1/experimental-models"
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"Authorization": f"Bearer {tool_token}"}).status_code == 401
        )
        response = client.get(
            path, params={"limit": 50, "data_mode": "LIVE_RESEARCH"}, headers=headers
        )
        assert response.status_code == 200
        body = response.json()
        assert identity in [item["id"] for item in body["items"]]
        assert all(item["data_mode"] == "REPLAY" for item in body["items"])
        for query in ({"limit": 51}, {"horizon_sessions": "5.0"}, {"horizon_sessions": 2}):
            assert client.get(path, params=query, headers=headers).status_code == 422
        invalid = client.get(path, params={"cursor": "../model"}, headers=headers)
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_CURSOR"
    disabled = settings.model_copy(update={"experimental_models": "DISABLED"})
    with TestClient(create_app(disabled)) as client:
        assert client.get(path, headers=headers).status_code == 503
