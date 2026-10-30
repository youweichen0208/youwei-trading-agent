import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.labels import build_label
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode
from yta_alpha.train_cli import TrainingInput
from yta_alpha.training import TrainConfig, save_artifact, train_fold
from yta_alpha.training_evidence_registry import TrainingEvidenceRegistry, verify_training_evidence

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def evidence_case(tmp_path_factory):
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Actual PostgreSQL required")
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test")
    migrate(url, ROOT / "infra/migrations")
    with psycopg.connect(url) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.training_evidence TO yta_app")
    config.update(user="yta_app", password="local-yta-app")
    spec = importlib.util.spec_from_file_location(
        "training_evidence_fixture", ROOT / "services/alpha-engine/tests/test_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calendar = TradingCalendar()
    _, snapshots, observations, split = module.experiment(calendar, 5)
    suffix = str(uuid4())
    snapshots = tuple(s.model_copy(update={"source_id": s.source_id + suffix}) for s in snapshots)
    labels = tuple(
        build_label(s, o, horizon_sessions=5, cutoff=o.as_of, calendar=calendar)
        for s, o in zip(snapshots, observations, strict=True)
    )
    inputs = TrainingInput(
        labels=labels,
        snapshots=snapshots,
        observations=observations,
        split_config=split,
        config=TrainConfig(rounds=10, min_data_in_leaf=2, num_leaves=4),
        fold_index=0,
        cutoff=observations[-1].as_of,
    )
    model = train_fold(
        labels,
        snapshots,
        observations,
        split_config=split,
        config=inputs.config,
        fold_index=0,
        cutoff=inputs.cutoff,
        calendar=calendar,
    )
    registry = TrainingEvidenceRegistry(make_conninfo(**config), calendar)
    path = tmp_path_factory.mktemp("training-evidence") / "model.json"
    digest = save_artifact(model, path)
    identity = registry.models.register(path, digest)
    return registry, identity, inputs, url


def test_training_evidence_concurrent_dedup_replay_and_model_binding(evidence_case):
    registry, model_id, spec, _ = evidence_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(
            pool.map(lambda _: registry.create(model_id, spec, mode=DataMode.REPLAY), range(4))
        )
    assert len(set(ids)) == 1
    value = registry.get(ids[0], mode=DataMode.REPLAY)
    assert value["input"] == spec.model_dump(mode="json")
    assert value["model_id"] == model_id
    assert value["report"]["status"] == "MATCHED"
    assert value["report"]["mismatched_fields"] == []
    assert value["report"]["promotion_allowed"] is False
    registered = registry.models.get(model_id)
    assert value["artifact_file_sha256"] == registered.artifact_file_sha256
    assert value["registration_content_hash"] == content_hash(registered.model_dump(mode="json"))
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.training_evidence WHERE id=%s", (ids[0],)
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (ids[0],)
            ).fetchone()["n"]
            == 1
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                "INSERT INTO yta.training_evidence(id,model_id,data_mode,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s)",
                (
                    "training-evidence-" + "0" * 64,
                    "lightgbm-experimental-" + "0" * 64,
                    "REPLAY",
                    "0" * 64,
                    Jsonb(value | {"model_id": "lightgbm-experimental-" + "0" * 64}),
                ),
            )


@pytest.mark.parametrize("role", ["owner", "runtime"])
@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE yta.training_evidence SET payload=payload",
        "DELETE FROM yta.training_evidence",
        "TRUNCATE yta.training_evidence",
    ],
)
def test_training_evidence_is_immutable(evidence_case, role, operation):
    registry, _, _, owner = evidence_case
    with psycopg.connect(owner if role == "owner" else registry.models.url) as connection:
        with pytest.raises(psycopg.Error) as error:
            connection.execute(operation)
        assert error.value.sqlstate == ("55000" if role == "owner" else "42501")


def test_training_evidence_audit_failure_rolls_back(evidence_case, monkeypatch):
    registry, model_id, spec, _ = evidence_case
    spec = spec.model_copy(update={"config": spec.config.model_copy(update={"rounds": 13})})
    expected = registry.replay(model_id, spec, mode=DataMode.REPLAY)
    identity = "training-evidence-" + content_hash(expected)
    connect = registry._connect

    class BrokenAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            if "INSERT INTO yta.audit_events" in sql:
                raise RuntimeError("injected training audit failure")
            return self.connection.execute(sql, params)

    @contextmanager
    def broken():
        with connect() as connection:
            yield BrokenAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected"):
        registry.create(model_id, spec, mode=DataMode.REPLAY)
    with connect() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.training_evidence WHERE id=%s", (identity,)
            ).fetchone()
            is None
        )


def test_training_evidence_preserves_mismatch_and_rejects_rehashed_claims(evidence_case):
    registry, model_id, spec, _ = evidence_case
    altered = spec.model_copy(update={"config": spec.config.model_copy(update={"rounds": 11})})
    identity = registry.create(model_id, altered, mode=DataMode.REPLAY)
    payload = registry.get(identity, mode=DataMode.REPLAY)
    assert payload["report"]["status"] == "MISMATCH"
    assert "config" in payload["report"]["mismatched_fields"]
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.training_evidence WHERE id=%s", (identity,)
        ).fetchone()
    for mutate in (
        lambda p: p["report"].update(status="MATCHED", mismatched_fields=[]),
        lambda p: p["input"]["config"].update(rounds=12),
        lambda p: p.update(artifact_file_sha256="0" * 64),
        lambda p: p.update(registration_content_hash="0" * 64),
    ):
        changed = deepcopy(row)
        mutate(changed["payload"])
        report = changed["payload"]["report"]
        report["content_hash"] = content_hash(
            {k: v for k, v in report.items() if k != "content_hash"}
        )
        changed["content_hash"] = content_hash(changed["payload"])
        changed["id"] = "training-evidence-" + changed["content_hash"]
        with pytest.raises(DomainError) as error:
            verify_training_evidence(changed, payload)
        assert error.value.code == "TRAINING_EVIDENCE_INTEGRITY_ERROR"
    old_code = deepcopy(row)
    report = old_code["payload"]["report"]
    report["verifier_code_sha256"] = "0" * 64
    report["content_hash"] = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    old_code["content_hash"] = content_hash(old_code["payload"])
    old_code["id"] = "training-evidence-" + old_code["content_hash"]
    assert verify_training_evidence(old_code, payload) == old_code["payload"]


def test_training_evidence_mode_isolation_and_native_unavailable(evidence_case, monkeypatch):
    from yta_alpha import training

    registry, model_id, spec, _ = evidence_case
    identity = registry.create(model_id, spec, mode=DataMode.REPLAY)
    for mode in (DataMode.DEMO, DataMode.LIVE_RESEARCH):
        with pytest.raises(DomainError) as error:
            registry.get(identity, mode=mode)
        assert error.value.status == 404
        with pytest.raises(DomainError) as error:
            registry.create(model_id, spec, mode=mode)
        assert error.value.status == 404

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        registry.get(identity, mode=DataMode.REPLAY)
    assert error.value.code == "TRAINING_UNAVAILABLE"
    with pytest.raises(DomainError):
        registry.create(model_id, spec, mode=DataMode.REPLAY)


def test_training_evidence_cli_roundtrip_and_mode_errors(evidence_case, tmp_path):
    import json
    import subprocess
    import sys

    registry, model_id, spec, _ = evidence_case
    inputs = tmp_path / "input.json"
    inputs.write_text(spec.model_dump_json())
    env = {**os.environ, "YTA_MODEL_DATABASE_URL": registry.models.url, "YTA_DATA_MODE": "REPLAY"}
    command = [
        sys.executable,
        "-m",
        "yta_alpha.train_cli",
        "register-training-evidence",
        "--input",
        str(inputs),
        "--model-id",
        model_id,
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    saved = json.loads(result.stdout)
    assert saved["status"] == "MATCHED" and saved["qualification"] == "UNVERIFIED"
    assert not {"input", "model_text", "database_url"} & saved.keys()
    read = [
        sys.executable,
        "-m",
        "yta_alpha.train_cli",
        "read-training-evidence",
        "--id",
        saved["id"],
    ]
    result = subprocess.run(read, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and json.loads(result.stdout) == saved
    result = subprocess.run(
        read, env=env | {"YTA_DATA_MODE": "DEMO"}, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error_code": "TRAINING_EVIDENCE_NOT_FOUND"}


@pytest.mark.parametrize("matched", [True, False])
def test_training_evidence_http_private_projection_and_auth(evidence_case, matched):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings
    from yta_alpha.training_evidence_contracts import TrainingEvidenceResponse
    from yta_alpha.training_evidence_reports import training_evidence_response

    registry, model_id, spec, _ = evidence_case
    if not matched:
        spec = spec.model_copy(update={"config": spec.config.model_copy(update={"rounds": 11})})
    identity = registry.create(model_id, spec, mode=DataMode.REPLAY)
    expected = training_evidence_response(registry, identity, mode=DataMode.REPLAY).model_dump(
        mode="json"
    )
    token, tool = "synthetic-training-service-12345678", "synthetic-training-tools-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/training-evidence/" + identity
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        saved = response.json()
        assert (
            saved
            == expected
            == TrainingEvidenceResponse.model_validate(saved).model_dump(mode="json")
        )
        assert set(saved) == {
            "id",
            "content_hash",
            "model_id",
            "data_mode",
            "registration_content_hash",
            "artifact_file_sha256",
            "input_hash",
            "report",
        }
        assert saved["report"]["status"] == ("MATCHED" if matched else "MISMATCH")
        assert client.get(path, headers=headers).json() == saved
        assert client.get("/internal/v1/training-evidence/bad", headers=headers).status_code == 422
        assert (
            client.get(
                "/internal/v1/training-evidence/training-evidence-" + "0" * 64, headers=headers
            ).status_code
            == 404
        )
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_training_evidence_http_default_off_mode_and_unavailable(evidence_case, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, model_id, spec, _ = evidence_case
    identity = registry.create(model_id, spec, mode=DataMode.REPLAY)
    token = "synthetic-training-service-12345678"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/training-evidence/" + identity
    for update in ({}, {"experimental_models": "ENABLED", "database_url": None}):
        with TestClient(create_app(settings.model_copy(update=update))) as client:
            assert client.get(path, headers=headers).status_code == 503
    enabled = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(create_app(enabled.model_copy(update={"data_mode": DataMode.DEMO}))) as client:
        assert client.get(path, headers=headers).status_code == 404

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "private runtime details", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(enabled)) as client:
        response = client.get(path, headers=headers)
        assert (
            response.status_code == 503
            and response.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
        )


def test_training_evidence_contract_rejects_rehashed_field_partition_and_binding(evidence_case):
    from pydantic import ValidationError
    from yta_alpha.training_evidence_contracts import TrainingEvidenceResponse
    from yta_alpha.training_evidence_reports import training_evidence_response

    registry, model_id, spec, _ = evidence_case
    identity = registry.create(model_id, spec, mode=DataMode.REPLAY)
    saved = training_evidence_response(registry, identity, mode=DataMode.REPLAY).model_dump(
        mode="json"
    )
    for mutate in (
        lambda p: p["report"]["matched_fields"].pop(),
        lambda p: p["report"]["matched_fields"].append("model_text"),
        lambda p: p["report"]["matched_fields"].reverse(),
        lambda p: p["report"].update(status="MISMATCH"),
        lambda p: p["report"].update(promotion_allowed=0),
        lambda p: p["report"].update(expected_model_sha256="0" * 64),
        lambda p: p["report"]["expected_source_hashes"].update(extra="0" * 64),
        lambda p: p.update(input_hash="0" * 64),
        lambda p: p.update(registration_content_hash="0" * 64),
        lambda p: p.update(input={"private": "no"}),
    ):
        value = deepcopy(saved)
        mutate(value)
        report = value["report"]
        report["content_hash"] = content_hash(
            {k: v for k, v in report.items() if k != "content_hash"}
        )
        with pytest.raises(ValidationError):
            TrainingEvidenceResponse.model_validate(value)


def test_training_evidence_catalog_pagination_without_retraining(evidence_case, monkeypatch):
    from yta_alpha import training
    from yta_alpha.training_evidence_catalog import training_evidence_page

    registry, model_id, spec, _ = evidence_case
    ids = {
        registry.create(
            model_id,
            spec.model_copy(update={"config": spec.config.model_copy(update={"rounds": rounds})}),
            mode=DataMode.REPLAY,
        )
        for rounds in (10, 11, 14)
    }
    with registry._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
        expected = [
            r["id"]
            for r in connection.execute(
                "SELECT id FROM yta.training_evidence WHERE model_id=%s "
                "ORDER BY created_at DESC,id DESC",
                (model_id,),
            )
        ]

    def forbidden():
        raise AssertionError("Catalog must not load native training library")

    monkeypatch.setattr(training, "library", forbidden)
    seen, cursor = [], None
    for _ in range(len(expected) + 1):
        page = training_evidence_page(registry, model_id, DataMode.REPLAY, limit=1, cursor=cursor)
        seen.extend(item.id for item in page.items)
        for item in page.items:
            assert item.verification == "stored-integrity-checked-retraining-on-detail"
            assert item.qualification == "UNVERIFIED"
            assert "input" not in item.model_dump() and "report" not in item.model_dump()
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == expected and ids.issubset(seen)
    assert len(seen) == len(set(seen))
    for value in (True, 0, 51):
        with pytest.raises(DomainError, match="Limit"):
            training_evidence_page(registry, model_id, DataMode.REPLAY, limit=value)
    for value in ("bad", "training-evidence-" + "0" * 64):
        with pytest.raises(DomainError) as error:
            training_evidence_page(registry, model_id, DataMode.REPLAY, cursor=value)
        assert error.value.code == "INVALID_CURSOR"
    with pytest.raises(DomainError) as error:
        training_evidence_page(registry, model_id, DataMode.DEMO)
    assert error.value.status == 404
    with registry._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_training_evidence_catalog_rehashed_binding_rejected(evidence_case):
    from yta_alpha.training_evidence_catalog import stored_summary

    registry, model_id, spec, _ = evidence_case
    identity = registry.create(model_id, spec, mode=DataMode.REPLAY)
    registered = registry.models.get(model_id)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.training_evidence WHERE id=%s", (identity,)
        ).fetchone()
    assert stored_summary(row, registered, model_id, DataMode.REPLAY).status == "MATCHED"
    for mutate in (
        lambda p: p.update(artifact_file_sha256="0" * 64),
        lambda p: p.update(registration_content_hash="0" * 64),
        lambda p: p.update(private_extra="not allowed"),
        lambda p: p["input"]["config"].update(rounds=15),
        lambda p: p["report"].update(
            expected_artifact_content_hash="0" * 64, replayed_artifact_content_hash="0" * 64
        ),
        lambda p: p["report"].update(
            expected_model_sha256="0" * 64, replayed_model_sha256="0" * 64
        ),
        lambda p: p["report"].update(expected_source_hashes={}, replayed_source_hashes={}),
    ):
        changed = deepcopy(row)
        mutate(changed["payload"])
        report = changed["payload"]["report"]
        report["content_hash"] = content_hash(
            {k: v for k, v in report.items() if k != "content_hash"}
        )
        changed["content_hash"] = content_hash(changed["payload"])
        changed["id"] = "training-evidence-" + changed["content_hash"]
        with pytest.raises(DomainError) as error:
            stored_summary(changed, registered, model_id, DataMode.REPLAY)
        assert error.value.code == "TRAINING_EVIDENCE_INTEGRITY_ERROR"


def test_training_evidence_catalog_http_guards(evidence_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, model_id, _, _ = evidence_case
    token, tool = "catalog-service-synthetic-12345678", "catalog-tool-synthetic-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.models.url),
    )
    path = f"/internal/v1/experimental-models/{model_id}/training-evidence"
    headers = {"Authorization": "Bearer " + token}
    for update, status in (
        ({}, 503),
        ({"experimental_models": "ENABLED", "database_url": None}, 503),
        ({"experimental_models": "ENABLED", "data_mode": DataMode.DEMO}, 404),
        ({"experimental_models": "ENABLED"}, 200),
    ):
        with TestClient(create_app(settings.model_copy(update=update))) as client:
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
            assert client.get(path, headers=headers).status_code == status
            assert client.get(path + "?limit=0", headers=headers).status_code == 422
