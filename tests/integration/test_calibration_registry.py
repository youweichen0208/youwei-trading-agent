import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from yta_alpha.calibrate_cli import CalibrationInput
from yta_alpha.calibration_registry import PREFIX, CalibrationRegistry, verify_record
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.migrate import migrate
from yta_alpha.training import save_artifact

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Actual PostgreSQL requires YTA_TEST_DATABASE_URL")
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test")
    migrate(url, ROOT / "infra/migrations")
    with psycopg.connect(url) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.calibration_records TO yta_app")
    config.update(user="yta_app", password="local-yta-app")
    location = ROOT / "services/alpha-engine/tests"
    spec = importlib.util.spec_from_file_location(
        "calibration_fixture", location / "test_calibration.py"
    )
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(location))
        spec.loader.exec_module(module)
        model, labels, inputs, outcomes, calendar = module.experiment.__wrapped__()
    model = model.model_copy(
        update={
            "source_hashes": {**model.source_hashes, "test-scenario": content_hash(uuid4().hex)}
        }
    )
    registry = CalibrationRegistry(
        os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL", make_conninfo(**config)), calendar
    )
    artifact = tmp_path_factory.mktemp("calibration-registry") / "model.json"
    digest = save_artifact(model, artifact)
    model_id = registry.models.register(artifact, digest)
    indices = (*range(45, 50), *range(55, 60))
    source = CalibrationInput(
        calibration_labels=labels[45:50],
        evaluation_labels=labels[55:60],
        snapshots=tuple(inputs[i] for i in indices),
        observations=tuple(outcomes[i] for i in indices),
        cutoff=outcomes[-1].as_of,
    )
    return registry, model_id, source, model


def test_concurrent_calibration_registers_one_record_and_audit(setup):
    registry, model_id, source, _ = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: registry.create(model_id, source), range(4)))
    assert len(set(ids)) == 1
    value = registry.get(ids[0])
    assert value["input"] == source.model_dump(mode="json")
    assert value["report"]["status"] == "EXPERIMENTAL"
    with registry.models._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.calibration_records WHERE id=%s", (ids[0],)
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events "
                "WHERE action='model.calibration_registered' AND resource_id=%s",
                (ids[0],),
            ).fetchone()["n"]
            == 1
        )


def test_audit_failure_rolls_back_calibration(setup, monkeypatch):
    from datetime import timedelta

    registry, model_id, source, _ = setup
    source = source.model_copy(update={"cutoff": source.cutoff + timedelta(seconds=1)})
    original = registry.models._connect

    class Failing:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield Failing(connection)

    with original() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.calibration_records").fetchone()[
            "n"
        ]
    monkeypatch.setattr(registry.models, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        registry.create(model_id, source)
    monkeypatch.setattr(registry.models, "_connect", original)
    with original() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.calibration_records").fetchone()["n"]
            == before
        )
    assert (
        registry.get(registry.create(model_id, source))["input"]["cutoff"]
        == source.model_dump(mode="json")["cutoff"]
    )


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_calibration_facts_are_immutable(setup, verb):
    registry, model_id, source, _ = setup
    identity = registry.create(model_id, source)
    statements = {
        "UPDATE": "UPDATE yta.calibration_records SET id=id WHERE id=%s",
        "DELETE": "DELETE FROM yta.calibration_records WHERE id=%s",
        "TRUNCATE": "TRUNCATE yta.calibration_records",
    }
    with pytest.raises(psycopg.Error):
        with registry.models._connect() as connection:
            connection.execute(statements[verb], (identity,) if verb != "TRUNCATE" else None)
    assert registry.get(identity)["model_id"] == model_id


def test_replay_rejects_false_metrics_even_with_rehashed_outer_record(setup):
    registry, model_id, source, model = setup
    identity = registry.create(model_id, source)
    payload = registry.get(identity)
    payload["report"]["reliability"]["brier"] = "0.999999999999"
    digest = content_hash(payload)
    with pytest.raises(DomainError) as error:
        verify_record(PREFIX + digest, digest, payload, model, registry.calendar)
    assert error.value.code == "CALIBRATION_INTEGRITY_ERROR"


def test_private_cli_register_and_read(setup, tmp_path):
    registry, model_id, source, _ = setup
    path = tmp_path / "input.json"
    path.write_text(source.model_dump_json())
    env = {**os.environ, "YTA_MODEL_DATABASE_URL": registry.models.url}
    command = [sys.executable, "-m", "yta_alpha.calibration_registry"]
    created = subprocess.run(
        [*command, "create", "--model-id", model_id, "--input", str(path)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    identity = json.loads(created.stdout)["id"]
    path.unlink()
    read = subprocess.run(
        [*command, "get", "--id", identity], env=env, capture_output=True, text=True, check=True
    )
    payload = json.loads(read.stdout)
    assert payload["report"] == registry.get(identity)["report"]
    assert "input" not in payload and "snapshots" not in payload


def later_snapshot(source, calendar, sessions=5):
    from datetime import timedelta

    snapshot = source.snapshots[-1]
    bars = []
    for bar in snapshot.bars:
        day = calendar.after(bar.session, sessions)
        at = calendar.close_at(day) + timedelta(minutes=10)
        bars.append(bar.model_copy(update={"session": day, "available_at": at, "retrieved_at": at}))
    return snapshot.model_copy(
        update={
            "bars": tuple(bars),
            "as_of": bars[-1].available_at,
            "actions_verified_through": bars[-1].session,
        }
    )


def test_calibrated_http_is_explicit_private_and_read_only(setup):
    from decimal import Decimal

    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.calibration import calibrated_probability
    from yta_alpha.settings import Settings

    registry, model_id, source, _ = setup
    identity = registry.create(model_id, source)
    snapshot = later_snapshot(source, registry.calendar)
    assert snapshot.as_of == source.cutoff  # inclusive boundary of report availability
    token = "synthetic-calibrated-service-token-12345"
    tool_token = "synthetic-calibrated-tools-token-1234567"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool_token),
        database_url=SecretStr(registry.models.url),
    )
    body = {"snapshot": snapshot.model_dump(mode="json"), "horizon_sessions": 5}
    path = f"/internal/v1/experimental-models/{model_id}/calibrations/{identity}/predict"
    headers = {"Authorization": f"Bearer {token}"}
    query = (
        "SELECT (SELECT count(*) FROM yta.predictions) AS predictions, "
        "(SELECT count(*) FROM yta.prediction_drafts) AS drafts, "
        "(SELECT count(*) FROM yta.calibration_records) AS calibrations, "
        "(SELECT count(*) FROM yta.audit_events) AS audits"
    )
    with registry.models._connect() as connection:
        before = connection.execute(query).fetchone()
    with TestClient(create_app(settings, lambda: snapshot.as_of, registry.calendar)) as client:
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
        knots = tuple(
            (Decimal(x), Decimal(y)) for x, y in registry.get(identity)["report"]["knots"]
        )
        assert Decimal(result["probability_up"]) == calibrated_probability(
            knots, Decimal(result["expected_return"])
        )
        assert result["calibration_id"] == identity
        assert result["probability_qualification"] == "UNVERIFIED"
        assert result["status"] == "EXPERIMENTAL" and "draft_id" not in result
        plain = client.post(
            f"/internal/v1/experimental-models/{model_id}/predict", json=body, headers=headers
        )
        assert plain.status_code == 200 and plain.json()["probability_up"] is None
        unknown = path.replace(identity, PREFIX + "0" * 64)
        assert client.post(unknown, json=body, headers=headers).status_code == 404
        mismatch = path.replace(model_id, "lightgbm-experimental-" + "0" * 64)
        assert (
            client.post(mismatch, json=body, headers=headers).json()["error"]["code"]
            == "CALIBRATION_MODEL_MISMATCH"
        )
        assert (
            client.post(path, json={**body, "probability_up": "1"}, headers=headers).status_code
            == 422
        )
    with registry.models._connect() as connection:
        assert connection.execute(query).fetchone() == before


def test_calibration_report_time_and_real_registration_time_are_enforced(setup):
    from yta_alpha.calibrated_predictions import calibrated_predict
    from yta_alpha.experimental_predictions import ExperimentalRequest

    registry, model_id, source, _ = setup
    identity = registry.create(model_id, source)
    # First evaluation input is after the fit labels, but before the complete report cutoff.
    old = source.snapshots[len(source.calibration_labels)]
    assert old.as_of > max(row.label_available_at for row in source.calibration_labels)
    with pytest.raises(DomainError) as error:
        calibrated_predict(
            registry,
            model_id,
            identity,
            ExperimentalRequest(snapshot=old, horizon_sessions=5),
            now=source.cutoff,
            replay=True,
        )
    assert error.value.code == "CALIBRATION_NOT_AVAILABLE"
    snapshot = later_snapshot(source, registry.calendar)
    _, actual_registered_at = registry.get_with_timestamp(identity)
    assert actual_registered_at > snapshot.as_of
    with pytest.raises(DomainError) as error:
        calibrated_predict(
            registry,
            model_id,
            identity,
            ExperimentalRequest(snapshot=snapshot, horizon_sessions=5),
            now=actual_registered_at,
            replay=False,
        )
    assert error.value.code == "CALIBRATION_NOT_AVAILABLE"


def test_calibrated_route_remains_disabled_by_default(setup):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, model_id, source, _ = setup
    token = "synthetic-calibrated-service-token-12345"
    settings = Settings(
        _env_file=None, alpha_token=SecretStr(token), database_url=SecretStr(registry.models.url)
    )
    path = f"/internal/v1/experimental-models/{model_id}/calibrations/{PREFIX + '0' * 64}/predict"
    body = {"snapshot": source.snapshots[0].model_dump(mode="json"), "horizon_sessions": 5}
    with TestClient(create_app(settings, calendar=registry.calendar)) as client:
        response = client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EXPERIMENTAL_MODELS_DISABLED"


def test_registered_price_evaluation_uses_only_independent_rows_and_no_writes(setup):
    from decimal import Decimal, localcontext

    from yta_alpha.model_evaluation import evaluate_registered

    registry, model_id, source, _ = setup
    identity = registry.create(model_id, source)
    with registry.models._connect() as connection:
        before = connection.execute(
            "SELECT (SELECT count(*) FROM yta.audit_events) AS audits, "
            "(SELECT count(*) FROM yta.evaluation_reports) AS reports"
        ).fetchone()
    result = evaluate_registered(registry, identity)
    assert result["qualification"] == "UNVERIFIED"
    assert result["cohort"]["data_mode"] == "REPLAY"
    assert len(result["manifest"]) == len(source.evaluation_labels) == 5
    assert result["cohort"]["label_ids"] == registry.get(identity)["report"]["evaluation_ids"]
    assert not set(result["cohort"]["label_ids"]) & set(
        registry.get(identity)["report"]["calibration_ids"]
    )
    with localcontext() as context:
        context.prec = 50
        rows = result["manifest"]
        mae = (
            sum(
                abs(Decimal(row["model_expected_return"]) - Decimal(row["actual_return"]))
                for row in rows
            )
            / 5
        )
        zero = sum(abs(Decimal(row["actual_return"])) for row in rows) / 5
        assert Decimal(result["metrics"]["model"]["mae"]) == mae.quantize(Decimal("1e-12"))
        assert Decimal(result["metrics"]["zero-return-v1"]["mae"]) == zero.quantize(
            Decimal("1e-12")
        )
    assert result["metrics"]["momentum20-v1"]["sample_count"] == 5
    assert result["momentum_unavailable_reason"] is None
    digest = result.pop("content_hash")
    assert digest == content_hash(result)
    with registry.models._connect() as connection:
        after = connection.execute(
            "SELECT (SELECT count(*) FROM yta.audit_events) AS audits, "
            "(SELECT count(*) FROM yta.evaluation_reports) AS reports"
        ).fetchone()
    assert after == before


def test_registered_comparison_requires_same_evidence_and_cutoff(setup, tmp_path, price_registry):
    from datetime import timedelta

    from yta_alpha.model_evaluation import compare_registered

    registry, model_id, source, model = setup
    first = registry.create(model_id, source)
    # Distinct synthetic provenance identity, intentionally identical native weights.
    variant = model.model_copy(
        update={
            "source_hashes": {
                **model.source_hashes,
                "test-comparison": content_hash(uuid4().hex),
            }
        }
    )
    path = tmp_path / "variant.json"
    model_two = registry.models.register(path, save_artifact(variant, path))
    second = registry.create(model_two, source)
    compared = compare_registered(registry, [first, second])
    assert len(compared["reports"]) == 2
    assert compared["reports"][0]["metrics"] == compared["reports"][1]["metrics"]
    later = registry.create(
        model_two, source.model_copy(update={"cutoff": source.cutoff + timedelta(seconds=1)})
    )
    with pytest.raises(DomainError) as error:
        compare_registered(registry, [first, later])
    assert error.value.code == "EVALUATION_COHORT_MISMATCH"
    # Same dates/cutoff but a revised target observation is a different cohort too.
    from decimal import Decimal

    from yta_alpha.forecast import snapshot_hash
    from yta_alpha.labels import build_label

    label = source.evaluation_labels[-1]
    observations = list(source.observations)
    index = next(
        i
        for i, row in enumerate(observations)
        if snapshot_hash(row) == label.observation_snapshot_hash
    )
    observation = observations[index]
    bar = observation.bars[-1]
    updated_bar = bar.model_copy(
        update={name: getattr(bar, name) + Decimal(1) for name in ("open", "high", "low", "close")}
    )
    observations[index] = observation.model_copy(
        update={"bars": (*observation.bars[:-1], updated_bar)}
    )
    base = next(row for row in source.snapshots if snapshot_hash(row) == label.base_snapshot_hash)
    updated_label = build_label(
        base,
        observations[index],
        horizon_sessions=5,
        cutoff=source.cutoff,
        calendar=registry.calendar,
    )
    revised = source.model_copy(
        update={
            "observations": tuple(observations),
            "evaluation_labels": (*source.evaluation_labels[:-1], updated_label),
        }
    )
    revised_id = registry.create(model_two, revised)
    saved_registry = price_registry[0]
    first_saved = saved_registry.create(first)
    revised_saved = saved_registry.create(revised_id)
    with pytest.raises(DomainError) as saved_error:
        saved_registry.compare([first_saved, revised_saved])
    assert saved_error.value.code == "EVALUATION_COHORT_MISMATCH"
    with pytest.raises(DomainError) as error:
        compare_registered(registry, [first, revised_id])
    assert error.value.code == "EVALUATION_COHORT_MISMATCH"
    for ids in ([first], [first, first], [first] * 11):
        with pytest.raises(DomainError) as error:
            compare_registered(registry, ids)
        assert error.value.code == "INVALID_COMPARISON"


def test_registered_price_evaluation_cli_is_reproducible(setup):
    from yta_alpha.model_evaluation import evaluate_registered

    registry, model_id, source, _ = setup
    identity = registry.create(model_id, source)
    result = subprocess.run(
        [sys.executable, "-m", "yta_alpha.model_evaluation", "--calibration-id", identity],
        env={**os.environ, "YTA_MODEL_DATABASE_URL": registry.models.url},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == evaluate_registered(registry, identity)

    invalid = subprocess.run(
        [sys.executable, "-m", "yta_alpha.model_evaluation", "--calibration-id", "missing"],
        env={**os.environ, "YTA_MODEL_DATABASE_URL": registry.models.url},
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    assert json.loads(invalid.stdout) == {"error_code": "MODEL_EVALUATION_FAILED"}
    assert registry.models.url not in invalid.stdout + invalid.stderr


@pytest.fixture
def price_registry(setup):
    from datetime import timedelta

    from yta_alpha.model_evaluation_registry import ModelEvaluationRegistry

    calibrations, model_id, source, _ = setup
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_evaluation_records TO yta_app")
    # Separate immutable calibration identities keep rollback and audit assertions exact.
    source = source.model_copy(
        update={"cutoff": source.cutoff + timedelta(microseconds=uuid4().int % 100000000 + 1)}
    )
    identity = calibrations.create(model_id, source)
    return ModelEvaluationRegistry(calibrations.models.url, calibrations.calendar), identity


def test_price_report_registration_is_concurrent_idempotent_and_audited(price_registry):
    registry, calibration_id = price_registry
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: registry.create(calibration_id), range(4)))
    assert len(set(identities)) == 1
    identity = identities[0]
    report = registry.get(identity)
    assert report["calibration_id"] == calibration_id
    with registry.calibrations.models._connect() as connection:
        rows = connection.execute(
            "SELECT actor_id,payload FROM yta.audit_events "
            "WHERE action='model.price_evaluation_registered' AND resource_id=%s",
            (identity,),
        ).fetchall()
    assert len(rows) == 1 and rows[0]["actor_id"] is None
    assert rows[0]["payload"]["qualification"] == "UNVERIFIED"


def test_price_report_audit_failure_rolls_back_fact(price_registry, monkeypatch):
    from yta_alpha.model_evaluation import evaluate_registered
    from yta_alpha.model_evaluation_registry import PREFIX as EVALUATION_PREFIX

    registry, calibration_id = price_registry
    identity = (
        EVALUATION_PREFIX
        + evaluate_registered(registry.calibrations, calibration_id)["content_hash"]
    )
    original = registry.calibrations.models._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected evaluation audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry.calibrations.models, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected evaluation audit failure"):
        registry.create(calibration_id)
    monkeypatch.setattr(registry.calibrations.models, "_connect", original)
    with original() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.model_evaluation_records WHERE id=%s", (identity,)
            ).fetchone()
            is None
        )
    assert registry.create(calibration_id) == identity


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_price_report_facts_cannot_be_mutated(price_registry, verb):
    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)
    statements = {
        "UPDATE": "UPDATE yta.model_evaluation_records SET id=id WHERE id=%s",
        "DELETE": "DELETE FROM yta.model_evaluation_records WHERE id=%s",
        "TRUNCATE": "TRUNCATE yta.model_evaluation_records",
    }
    with pytest.raises(psycopg.Error):
        with registry.calibrations.models._connect() as connection:
            connection.execute(statements[verb], (identity,) if verb != "TRUNCATE" else None)
    assert registry.get(identity)["qualification"] == "UNVERIFIED"


def test_price_report_replay_rejects_rehashed_false_metrics(price_registry):
    from copy import deepcopy

    from yta_alpha.model_evaluation_registry import PREFIX as EVALUATION_PREFIX
    from yta_alpha.model_evaluation_registry import verify_evaluation

    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)
    report = registry.get(identity)
    tampered = deepcopy(report)
    tampered["metrics"]["model"]["mae"] = "999.000000000000"
    tampered.pop("content_hash")
    digest = content_hash(tampered)
    tampered["content_hash"] = digest
    with pytest.raises(DomainError) as error:
        verify_evaluation(EVALUATION_PREFIX + digest, digest, tampered, report)
    assert error.value.code == "MODEL_EVALUATION_INTEGRITY_ERROR"
    historical = deepcopy(report)
    historical["code_sha256"]["price_evaluation"] = "1" * 64
    historical.pop("content_hash")
    old_digest = content_hash(historical)
    historical["content_hash"] = old_digest
    assert (
        verify_evaluation(EVALUATION_PREFIX + old_digest, old_digest, historical, report)
        == historical
    )


def test_price_report_cli_register_and_replay(price_registry):
    registry, calibration_id = price_registry
    command = [sys.executable, "-m", "yta_alpha.model_evaluation_registry"]
    env = {**os.environ, "YTA_MODEL_DATABASE_URL": registry.calibrations.models.url}
    created = subprocess.run(
        command + ["create", "--calibration-id", calibration_id],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    identity = json.loads(created.stdout)["id"]
    saved = subprocess.run(
        command + ["get", "--id", identity], env=env, capture_output=True, text=True, check=True
    )
    assert json.loads(saved.stdout) == registry.get(identity)
    with pytest.raises(DomainError) as error:
        registry.get("missing")
    assert error.value.code == "MODEL_EVALUATION_NOT_FOUND"


def test_price_report_database_rejects_missing_payload_identity(price_registry):
    from psycopg.types.json import Jsonb

    registry, calibration_id = price_registry
    payload = registry.calibrations.get(calibration_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        with registry.calibrations.models._connect() as connection:
            connection.execute(
                "INSERT INTO yta.model_evaluation_records "
                "(id,calibration_id,model_version_id,content_hash,data_mode,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (uuid4().hex, calibration_id, payload["model_id"], "0" * 64, "REPLAY", Jsonb({})),
            )


def test_private_price_report_http_auth_exact_payload_and_no_writes(price_registry):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)
    expected = registry.get(identity)
    service = "synthetic-price-report-service-token-123456789"
    tools = "synthetic-price-report-tools-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(service),
        alpha_tools_token=SecretStr(tools),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    path = "/internal/v1/experimental-evaluations/" + identity
    headers = {"Authorization": "Bearer " + service}
    with registry.calibrations.models._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tools}).status_code == 401
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert (
            response.json() == expected
        )  # Includes original +00:00 spelling, aliases, Decimal text.
        body = response.json()
        digest = body.pop("content_hash")
        assert content_hash(body) == digest
        assert client.get(path + "?data_mode=LIVE_RESEARCH", headers=headers).json() == expected
        assert (
            client.get("/internal/v1/experimental-evaluations/missing", headers=headers).status_code
            == 404
        )
    with registry.calibrations.models._connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone() == before
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503


def test_private_price_report_mode_guard_precedes_native_replay(price_registry, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import model_evaluation_registry as module
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("Foreign-mode evidence must not reach native replay")

    monkeypatch.setattr(module, "evaluate_registered", forbidden)
    token = "synthetic-price-report-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        data_mode="LIVE_RESEARCH",
        git_sha="1" * 40,
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/v1/experimental-evaluations/" + identity,
            headers={"Authorization": "Bearer " + token},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MODEL_EVALUATION_NOT_FOUND"


def test_private_price_report_unavailable_native_has_no_fixture_fallback(
    price_registry, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    token = "synthetic-price-report-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/v1/experimental-evaluations/" + identity,
            headers={"Authorization": "Bearer " + token},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
    assert "metrics" not in response.json()


def test_price_report_catalog_pages_and_scope(price_registry, setup, tmp_path):
    from datetime import timedelta

    from yta_alpha.model_evaluation_catalog import report_page
    from yta_alpha.model_evaluation_registry import PREFIX as EVAL_PREFIX
    from yta_alpha.models import DataMode

    registry, calibration_id = price_registry
    model_id = registry.calibrations.get(calibration_id)["model_id"]
    registry.create(calibration_id)
    source = setup[2]
    for offset in (1001, 1002):
        spec = source.model_copy(update={"cutoff": source.cutoff + timedelta(seconds=offset)})
        registry.create(registry.calibrations.create(model_id, spec))
    with registry.calibrations.models._connect() as connection:
        expected = connection.execute(
            "SELECT id FROM yta.model_evaluation_records WHERE model_version_id=%s "
            "AND data_mode='REPLAY' ORDER BY created_at DESC,id DESC",
            (model_id,),
        ).fetchall()
    cursor, seen = None, []
    while True:
        page = report_page(registry, model_id, DataMode.REPLAY, cursor=cursor, limit=2)
        assert len(page.items) <= 2
        for row in page.items:
            assert row.qualification == "UNVERIFIED" and row.sample_count == 5
            assert row.model_id == model_id and "metrics" not in row.model_dump()
        seen.extend(row.id for row in page.items)
        if page.next_cursor is None:
            break
        assert page.next_cursor != cursor
        cursor = page.next_cursor
    assert seen == [row["id"] for row in expected] and len(set(seen)) == len(seen)
    variant = setup[3].model_copy(
        update={
            "source_hashes": {
                **setup[3].source_hashes,
                "test-empty-catalog": content_hash(uuid4().hex),
            }
        }
    )
    path = tmp_path / "empty-model.json"
    other_model = registry.calibrations.models.register(path, save_artifact(variant, path))
    assert report_page(registry, other_model, DataMode.REPLAY).items == ()
    with pytest.raises(DomainError) as error:
        report_page(registry, other_model, DataMode.REPLAY, cursor=seen[0])
    assert error.value.code == "INVALID_CURSOR"
    for value in ("invalid", EVAL_PREFIX + "0" * 64):
        with pytest.raises(DomainError) as error:
            report_page(registry, model_id, DataMode.REPLAY, cursor=value)
        assert error.value.code == "INVALID_CURSOR"
    with pytest.raises(DomainError) as error:
        report_page(registry, model_id, DataMode.LIVE_RESEARCH, cursor=seen[0])
    assert error.value.code == "MODEL_NOT_FOUND"
    for limit in (0, 51, True):
        with pytest.raises(DomainError):
            report_page(registry, model_id, DataMode.REPLAY, limit=limit)


def test_price_report_catalog_http_auth_and_default_off(price_registry):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, calibration_id = price_registry
    identity = registry.create(calibration_id)
    model_id = registry.get(identity)["model_id"]
    token = "synthetic-evaluation-catalog-token-123456789"
    tool = "synthetic-evaluation-catalog-tool-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    path = "/internal/v1/experimental-models/" + model_id + "/evaluations"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
        response = client.get(path + "?limit=1&data_mode=LIVE_RESEARCH", headers=headers)
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == identity
        assert response.json()["items"][0]["data_mode"] == "REPLAY"
        assert client.get(path + "?limit=51", headers=headers).status_code == 422
        assert client.get(path + "?cursor=invalid", headers=headers).status_code == 422
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503


@pytest.fixture
def comparison_reports(price_registry, setup, tmp_path):
    registry, calibration_id = price_registry
    spec = CalibrationInput.model_validate(registry.calibrations.get(calibration_id)["input"])
    model = setup[3].model_copy(
        update={
            "source_hashes": {**setup[3].source_hashes, "comparison": content_hash(uuid4().hex)}
        }
    )
    path = tmp_path / "comparison-model.json"
    second_model = registry.calibrations.models.register(path, save_artifact(model, path))
    second_calibration = registry.calibrations.create(second_model, spec)
    return registry, [registry.create(calibration_id), registry.create(second_calibration)], spec


def test_persisted_report_comparison_preserves_identity_and_rejects_different_cutoff(
    comparison_reports,
):
    from datetime import timedelta

    from yta_alpha.models import DataMode

    registry, identities, spec = comparison_reports
    expected = [registry.get(identity) for identity in identities]
    with registry.calibrations.models._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()
    result = registry.compare(identities, data_mode=DataMode.REPLAY)
    assert result["reports"] == expected
    assert result["evaluation_ids"] == identities
    assert result["qualification"] == "UNVERIFIED"
    assert registry.compare(identities[::-1])["reports"] == expected[::-1]
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.model_evaluation_registry",
            "compare",
            "--id",
            identities[0],
            "--id",
            identities[1],
        ],
        env={**os.environ, "YTA_MODEL_DATABASE_URL": registry.calibrations.models.url},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(process.stdout) == result
    with registry.calibrations.models._connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone() == before
    different = registry.create(
        registry.calibrations.create(
            expected[0]["model_id"],
            spec.model_copy(update={"cutoff": spec.cutoff + timedelta(seconds=1)}),
        )
    )
    with pytest.raises(DomainError) as error:
        registry.compare([identities[0], different])
    assert error.value.code == "EVALUATION_COHORT_MISMATCH"
    for ids in (
        [],
        identities[:1],
        identities * 6,
        [identities[0]] * 2,
        ["invalid", identities[0]],
    ):
        with pytest.raises(DomainError) as error:
            registry.compare(ids)
        assert error.value.code == "INVALID_COMPARISON"


def test_report_comparison_http_gates_and_native_failure(comparison_reports, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, identities, _ = comparison_reports
    token = "synthetic-report-compare-service-token-123456789"
    tool = "synthetic-report-compare-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    path = "/internal/v1/experimental-evaluations/compare"
    headers = {"Authorization": "Bearer " + token}
    body = {"evaluation_ids": identities}
    with TestClient(create_app(settings)) as client:
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer " + tool}).status_code
            == 401
        )
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == registry.compare(identities)
        for payload in (
            {**body, "data_mode": "REPLAY"},
            {"evaluation_ids": identities[:1]},
            {"evaluation_ids": identities * 6},
            {"evaluation_ids": [identities[0]] * 2},
        ):
            assert client.post(path, json=payload, headers=headers).status_code == 422
        unknown = "model-evaluation-experimental-" + "0" * 64
        assert (
            client.post(
                path, json={"evaluation_ids": [unknown, identities[0]]}, headers=headers
            ).status_code
            == 404
        )
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.post(path, json=body, headers=headers).status_code == 503

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings)) as client:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
        assert "reports" not in response.json()
    with TestClient(
        create_app(settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 404  # Mode rejection happens before native replay.


@pytest.fixture
def proposal_case(comparison_reports):
    from yta_alpha.model_proposals import ModelProposalRegistry, ProposalRequest

    reports, ids, _ = comparison_reports
    owner = uuid4()
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_proposals TO yta_app")
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES(%s,%s)", (owner, str(owner) + "@proposal.test")
        )
    registry = ModelProposalRegistry(reports.calibrations.models.url, reports.calibrations.calendar)
    request = ProposalRequest(
        candidate_evaluation_id=ids[0],
        reference_evaluation_id=ids[1],
        rationale="Synthetic candidate for human review; not qualified",
    )
    return registry, owner, request


def test_proposals_concurrent_idempotency_owner_scope_and_no_promotion(proposal_case):
    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    key = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: registry.create(owner, key, DataMode.REPLAY, request), range(4))
        )
    assert results == [results[0]] * 4
    result = results[0]
    assert result["proposal"]["status"] == "SUBMITTED"
    assert result["proposal"]["qualification"] == "UNVERIFIED"
    assert registry.get(owner, DataMode.REPLAY, result["id"]) == result
    assert "champion" not in result["proposal"]
    with registry._connect() as connection:
        events = connection.execute(
            "SELECT actor_id FROM yta.audit_events "
            "WHERE action='model.proposal_submitted' AND resource_id=%s",
            (result["id"],),
        ).fetchall()
        assert events == [{"actor_id": owner}]
    for identity, mode in ((uuid4(), DataMode.REPLAY), (owner, DataMode.LIVE_RESEARCH)):
        with pytest.raises(DomainError) as error:
            registry.get(identity, mode, result["id"])
        assert error.value.code == "MODEL_PROPOSAL_NOT_FOUND"
    with pytest.raises(DomainError) as error:
        registry.create(
            owner, key, DataMode.REPLAY, request.model_copy(update={"rationale": "Changed"})
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(DomainError) as error:
        registry.create(owner, uuid4(), DataMode.LIVE_RESEARCH, request)
    assert error.value.code == "MODEL_EVALUATION_NOT_FOUND"


def test_proposal_references_and_baseline_only_submission(proposal_case, comparison_reports):
    from datetime import timedelta

    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    reports, identities, spec = comparison_reports
    same = request.model_copy(update={"reference_evaluation_id": request.candidate_evaluation_id})
    with pytest.raises(DomainError) as error:
        registry.create(owner, uuid4(), DataMode.REPLAY, same)
    assert error.value.code == "INVALID_PROPOSAL"
    other = reports.create(
        reports.calibrations.create(
            reports.get(identities[1])["model_id"],
            spec.model_copy(update={"cutoff": spec.cutoff + timedelta(seconds=1)}),
        )
    )
    with pytest.raises(DomainError) as error:
        registry.create(
            owner,
            uuid4(),
            DataMode.REPLAY,
            request.model_copy(update={"reference_evaluation_id": other}),
        )
    assert error.value.code == "EVALUATION_COHORT_MISMATCH"
    saved = registry.create(
        owner,
        uuid4(),
        DataMode.REPLAY,
        request.model_copy(update={"reference_evaluation_id": None}),
    )
    assert saved["proposal"]["reference_model_id"] is None
    assert saved["proposal"]["candidate_report_hash"]


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_proposal_runtime_and_owner_cannot_mutate(proposal_case, verb):
    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    registry.create(owner, uuid4(), DataMode.REPLAY, request)
    query = {
        "UPDATE": "UPDATE yta.model_proposals SET payload=payload WHERE false",
        "DELETE": "DELETE FROM yta.model_proposals WHERE false",
        "TRUNCATE": "TRUNCATE yta.model_proposals CASCADE",
    }[verb]
    with pytest.raises(psycopg.errors.InsufficientPrivilege), registry._connect() as connection:
        connection.execute(query)
    with (
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection,
    ):
        connection.execute(query)


def test_proposal_audit_failure_rolls_back(proposal_case, monkeypatch):
    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    key = uuid4()
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected proposal audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected proposal audit failure"):
        registry.create(owner, key, DataMode.REPLAY, request)
    with original() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.model_proposals WHERE request_id=%s", (key,)
            ).fetchone()
            is None
        )


def test_proposal_verification_rejects_rehashed_changes(proposal_case):
    from yta_alpha.model_proposals import verify_proposal
    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    result = registry.create(owner, uuid4(), DataMode.REPLAY, request)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.model_proposals WHERE id=%s", (result["id"],)
        ).fetchone()
    candidate = registry.reports.get(request.candidate_evaluation_id)
    reference = registry.reports.get(request.reference_evaluation_id)
    for changes in (
        {"status": "APPROVED"},
        {"candidate_model_id": reference["model_id"]},
        {"owner_id": str(uuid4())},
    ):
        payload = {**row["payload"], **changes}
        with pytest.raises(DomainError) as error:
            verify_proposal(
                {**row, "payload": payload, "content_hash": content_hash(payload)},
                candidate,
                reference,
            )
        assert error.value.code == "MODEL_PROPOSAL_INTEGRITY_ERROR"


def test_proposal_http_auth_identity_idempotency_and_exact_hash(proposal_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.settings import Settings

    registry, owner, request = proposal_case
    token = "synthetic-proposal-service-token-123456789"
    tool = "synthetic-proposal-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-proposals"
    key = str(uuid4())
    headers = {
        "Authorization": "Bearer " + token,
        "X-YTA-Owner-ID": str(owner),
        "Idempotency-Key": key,
    }
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path, json=body, headers={**headers, "Authorization": "Bearer " + tool}
            ).status_code
            == 401
        )
        for missing in ("X-YTA-Owner-ID", "Idempotency-Key"):
            assert (
                client.post(
                    path, json=body, headers={k: v for k, v in headers.items() if k != missing}
                ).status_code
                == 422
            )
        assert (
            client.post(
                path, json=body, headers={**headers, "Idempotency-Key": "not-uuid"}
            ).status_code
            == 422
        )
        for extra in (
            {"owner_id": str(uuid4())},
            {"status": "APPROVED"},
            {"data_mode": "LIVE_RESEARCH"},
            {"candidate_model_id": "injected"},
        ):
            assert client.post(path, json={**body, **extra}, headers=headers).status_code == 422
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200
        saved = first.json()
        assert saved["proposal"]["owner_id"] == str(owner)
        assert saved["proposal"]["request_id"] == key
        assert saved["proposal"]["status"] == "SUBMITTED"
        assert content_hash(saved["proposal"]) == saved["content_hash"]
        assert client.post(path, json=body, headers=headers).json() == saved
        conflict = client.post(
            path, json={**body, "rationale": "Different request"}, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        detail = path + "/" + saved["id"]
        assert client.get(detail, headers=headers).json() == saved
        assert client.get(detail).status_code == 401
        assert (
            client.get(detail, headers={**headers, "Authorization": "Bearer " + tool}).status_code
            == 401
        )
        assert (
            client.get(detail, headers={**headers, "X-YTA-Owner-ID": str(uuid4())}).status_code
            == 404
        )
        assert client.get(path + "/" + str(uuid4()), headers=headers).status_code == 404
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_proposals WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events "
                "WHERE action='model.proposal_submitted' AND resource_id=%s",
                (saved["id"],),
            ).fetchone()["n"]
            == 1
        )


def test_proposal_http_disabled_foreign_mode_and_native_unavailable_write_nothing(
    proposal_case, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, owner, request = proposal_case
    token = "synthetic-proposal-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-proposals"
    headers = {
        "Authorization": "Bearer " + token,
        "X-YTA-Owner-ID": str(owner),
        "Idempotency-Key": str(uuid4()),
    }
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(path, json=request.model_dump(mode="json"), headers=headers).status_code
            == 503
        )
        assert client.get(path + "/" + str(uuid4()), headers=headers).status_code == 503
    settings = settings.model_copy(update={"experimental_models": "ENABLED"})
    saved = registry.create(owner, uuid4(), DataMode.REPLAY, request)

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings)) as client:
        result = client.post(path, json=request.model_dump(mode="json"), headers=headers)
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
        assert "proposal" not in result.json()
    with TestClient(
        create_app(settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        assert (
            client.post(
                path + "?data_mode=REPLAY", json=request.model_dump(mode="json"), headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(path + "/" + saved["id"] + "?data_mode=REPLAY", headers=headers).status_code
            == 404
        )
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_proposals WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 1
        )


def test_proposal_history_pages_owner_mode_and_metadata_integrity(proposal_case, monkeypatch):
    from yta_alpha.model_proposal_catalog import proposal_metadata, proposal_page
    from yta_alpha.models import DataMode

    registry, owner, request = proposal_case
    saved = [registry.create(owner, uuid4(), DataMode.REPLAY, request) for _ in range(5)]
    other = uuid4()
    with registry._connect() as connection:
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES(%s,%s)", (other, str(other) + "@history.test")
        )
    foreign = registry.create(other, uuid4(), DataMode.REPLAY, request)

    def no_replay(*args, **kwargs):
        raise AssertionError("History discovery must not execute native report replay")

    monkeypatch.setattr(registry.reports, "get", no_replay)
    with registry._connect() as connection:
        expected = connection.execute(
            "SELECT id FROM yta.model_proposals WHERE owner_id=%s AND data_mode='REPLAY' "
            "ORDER BY created_at DESC,id DESC",
            (owner,),
        ).fetchall()
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()
        row = connection.execute(
            "SELECT * FROM yta.model_proposals WHERE id=%s", (saved[0]["id"],)
        ).fetchone()
    cursor, seen = None, []
    while True:
        page = proposal_page(registry, owner, DataMode.REPLAY, cursor=cursor, limit=2)
        for item in page.items:
            assert item.proposal.owner_id == owner
            assert item.proposal.qualification == "UNVERIFIED"
            assert item.model_dump(mode="json") == next(
                value for value in saved if value["id"] == str(item.id)
            )
        seen.extend(str(item.id) for item in page.items)
        if page.next_cursor is None:
            break
        assert page.next_cursor != cursor
        cursor = page.next_cursor
    assert seen == [str(item["id"]) for item in expected]
    assert len(set(seen)) == 5
    assert proposal_page(registry, uuid4(), DataMode.REPLAY).items == ()
    assert proposal_page(registry, owner, DataMode.LIVE_RESEARCH).items == ()
    for bad in (foreign["id"], str(uuid4()), "invalid"):
        with pytest.raises(DomainError) as error:
            proposal_page(registry, owner, DataMode.REPLAY, cursor=bad)
        assert error.value.code == "INVALID_CURSOR"
    with pytest.raises(DomainError) as error:
        proposal_page(registry, owner, DataMode.LIVE_RESEARCH, cursor=saved[0]["id"])
    assert error.value.code == "INVALID_CURSOR"
    for limit in (0, 51, True):
        with pytest.raises(DomainError):
            proposal_page(registry, owner, DataMode.REPLAY, limit=limit)
    for changes in ({"content_hash": "0" * 64}, {"request_hash": "0" * 64}, {"owner_id": other}):
        with pytest.raises(DomainError) as error:
            proposal_metadata({**row, **changes})
        assert error.value.code == "MODEL_PROPOSAL_INTEGRITY_ERROR"
    with registry._connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone() == before


def test_proposal_history_http_gates_and_server_scope(proposal_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, owner, request = proposal_case
    saved = registry.create(owner, uuid4(), DataMode.REPLAY, request)
    token = "synthetic-proposal-history-service-token-123456789"
    tool = "synthetic-proposal-history-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-proposals"
    headers = {"Authorization": "Bearer " + token, "X-YTA-Owner-ID": str(owner)}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={**headers, "Authorization": "Bearer " + tool}).status_code
            == 401
        )
        assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 422
        response = client.get(
            path + "?limit=1&data_mode=LIVE_RESEARCH&owner_id=" + str(uuid4()), headers=headers
        )
        assert response.status_code == 200
        assert response.json() == {"items": [saved], "next_cursor": None}
        assert client.get(path + "?limit=51", headers=headers).status_code == 422
        assert client.get(path + "?cursor=invalid", headers=headers).status_code == 422
        assert (
            client.get(path, headers={**headers, "X-YTA-Owner-ID": str(uuid4())}).json()["items"]
            == []
        )
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503


def test_quality_screen_replays_registered_evidence_and_never_promotes(comparison_reports):
    registry, identities, _ = comparison_reports
    policy = {
        "policy_version": "synthetic-test-only-v1",
        "data_mode": "REPLAY",
        "horizon_sessions": 5,
        "minimum_samples": 5,
        "maximum_mae": "1",
        "minimum_direction_accuracy": "0",
        "minimum_mae_improvement": "0",
        "require_reference": True,
    }
    with registry.calibrations.models._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    result = registry.assess_quality(identities[0], policy, reference_id=identities[1])
    assert result["candidate_evaluation_id"] == identities[0]
    assert result["reference_evaluation_id"] == identities[1]
    assert result["policy_hash"] == content_hash(
        policy | {"schema_version": "historical-quality-policy-v1"}
    )
    assert result["promotion_allowed"] is False
    assert result["qualification"] == "UNVERIFIED"
    reference = next(row for row in result["checks"] if row["name"] == "mae_improvement:reference")
    assert reference["observed"] == "0.000000000000"
    assert reference["passed"] is True
    assert registry.assess_quality(identities[0], policy, reference_id=identities[1]) == result
    assert registry.assess_quality(identities[0], policy)["numeric_pass"] is False
    with pytest.raises(DomainError):
        registry.assess_quality(identities[0], policy | {"data_mode": "LIVE_RESEARCH"})
    with registry.calibrations.models._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


@pytest.fixture
def quality_case(comparison_reports):
    from yta_alpha.model_quality_registry import ModelQualityRegistry

    reports, identities, _ = comparison_reports
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_quality_policies TO yta_app")
        connection.execute("GRANT SELECT,INSERT ON yta.model_quality_assessments TO yta_app")
    registry = ModelQualityRegistry(reports.calibrations.models.url, reports.calibrations.calendar)
    policy = {
        "policy_version": "synthetic-only-" + uuid4().hex,
        "data_mode": "REPLAY",
        "horizon_sessions": 5,
        "minimum_samples": 5,
        "maximum_mae": "1",
        "minimum_direction_accuracy": "0",
        "minimum_mae_improvement": "0",
        "require_reference": True,
    }
    return registry, identities, policy


def test_quality_registry_concurrent_rules_assessments_and_replay(quality_case):
    from yta_alpha.models import DataMode

    registry, identities, policy = quality_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        rules = list(pool.map(lambda _: registry.register_policy(policy), range(4)))
    assert len(set(rules)) == 1
    rule = rules[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: registry.create(rule, identities[0], reference_id=identities[1]), range(4)
            )
        )
    assert len(set(results)) == 1
    result = registry.get(results[0], data_mode=DataMode.REPLAY)
    assert result == registry.reports.assess_quality(
        identities[0], policy, reference_id=identities[1]
    )
    assert result["promotion_allowed"] is False
    with pytest.raises(DomainError) as error:
        registry.register_policy(policy | {"maximum_mae": "0.5"})
    assert error.value.status == 409
    with pytest.raises(DomainError):
        registry.get(results[0], data_mode=DataMode.LIVE_RESEARCH)
    with pytest.raises(DomainError):
        registry.get_policy(rule, data_mode=DataMode.LIVE_RESEARCH)
    missing = registry.create(rule, identities[0])
    assert missing != results[0]
    assert registry.get(missing)["numeric_pass"] is False
    with registry._connect() as connection:
        for identity in (rule, results[0], missing):
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
                ).fetchone()["n"]
                == 1
            )


@pytest.mark.parametrize("table", ["model_quality_policies", "model_quality_assessments"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_quality_facts_are_immutable_for_owner_and_runtime(quality_case, table, verb):
    from psycopg import sql

    registry, identities, policy = quality_case
    rule = registry.register_policy(policy)
    registry.create(rule, identities[0], reference_id=identities[1])
    statement = {
        "UPDATE": "UPDATE yta.{} SET payload=payload",
        "DELETE": "DELETE FROM yta.{}",
        "TRUNCATE": "TRUNCATE yta.{} CASCADE",
    }[verb]
    for url in (os.environ["YTA_TEST_DATABASE_URL"], registry.reports.calibrations.models.url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(sql.SQL(statement).format(sql.Identifier(table)))
            connection.rollback()


@pytest.mark.parametrize("kind", ["policy", "assessment"])
def test_quality_registry_audit_failure_rolls_back(quality_case, monkeypatch, kind):
    registry, identities, policy = quality_case
    rule = registry.register_policy(policy) if kind == "assessment" else None
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected quality audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected quality audit failure"):
        if kind == "policy":
            registry.register_policy(policy)
        else:
            registry.create(rule, identities[0], reference_id=identities[1])
    with original() as connection:
        if kind == "policy":
            assert (
                connection.execute(
                    "SELECT id FROM yta.model_quality_policies WHERE policy_version=%s",
                    (policy["policy_version"],),
                ).fetchone()
                is None
            )
        else:
            assert (
                connection.execute(
                    "SELECT id FROM yta.model_quality_assessments WHERE policy_id=%s", (rule,)
                ).fetchone()
                is None
            )


def test_quality_registry_rehashed_tampering_cannot_grant_approval(quality_case):
    from copy import deepcopy

    from yta_alpha.model_quality_registry import ASSESSMENT_PREFIX, verify_assessment

    registry, identities, policy = quality_case
    rule = registry.register_policy(policy)
    identity = registry.create(rule, identities[0], reference_id=identities[1])
    candidate, reference = (registry.reports.get(item) for item in identities)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.model_quality_assessments WHERE id=%s", (identity,)
        ).fetchone()
    for field, value in [
        ("promotion_allowed", True),
        ("numeric_pass", not row["payload"]["numeric_pass"]),
        ("qualification", "VERIFIED"),
    ]:
        modified = deepcopy(row)
        modified["payload"][field] = value
        modified["payload"].pop("content_hash")
        digest = content_hash(modified["payload"])
        modified.update(id=ASSESSMENT_PREFIX + digest, content_hash=digest)
        modified["payload"]["content_hash"] = digest
        with pytest.raises(DomainError, match="reproduced"):
            verify_assessment(modified, registry.get_policy(rule), candidate, reference)


def test_quality_http_auth_exact_registration_and_contracts(quality_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr, ValidationError
    from yta_alpha.api import create_app
    from yta_alpha.model_quality_contracts import QualityAssessmentResponse
    from yta_alpha.settings import Settings

    registry, identities, policy = quality_case
    rule = registry.register_policy(policy)
    token = "synthetic-quality-service-token-123456789"
    tool = "synthetic-quality-tools-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/historical-quality-assessments"
    rule_path = "/internal/v1/historical-quality-policies/" + rule
    body = {
        "policy_id": rule,
        "candidate_evaluation_id": identities[0],
        "reference_evaluation_id": identities[1],
    }
    with TestClient(create_app(settings)) as client:
        for auth_headers in ({}, {"Authorization": "Bearer " + tool}):
            assert client.post(path, json=body, headers=auth_headers).status_code == 401
            assert client.get(rule_path, headers=auth_headers).status_code == 401
        for injected in (
            {"data_mode": "LIVE_RESEARCH"},
            {"numeric_pass": True},
            {"policy": policy},
            {"promotion_allowed": True},
        ):
            assert client.post(path, json=body | injected, headers=headers).status_code == 422
        assert client.get(rule_path + "invalid", headers=headers).status_code == 422
        assert client.get(path + "/invalid", headers=headers).status_code == 422
        rule_response = client.get(rule_path, headers=headers)
        assert rule_response.status_code == 200
        assert rule_response.json()["policy"] == registry.get_policy(rule).model_dump(mode="json")
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 200
        saved = created.json()
        identity = "historical-quality-assessment-" + saved["content_hash"]
        assert saved == registry.get(identity)
        assert saved["promotion_allowed"] is False
        assert client.post(path, json=body, headers=headers).json() == saved
        assert client.get(path + "/" + identity, headers=headers).json() == saved
        assert (
            client.get(
                path + "/historical-quality-assessment-" + "0" * 64, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/internal/v1/historical-quality-policies/historical-quality-policy-" + "0" * 64,
                headers=headers,
            ).status_code
            == 404
        )
        assert QualityAssessmentResponse.model_validate(saved).model_dump(mode="json") == saved
        with pytest.raises(ValidationError):
            QualityAssessmentResponse.model_validate(
                saved | {"numeric_pass": not saved["numeric_pass"]}
            )
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_quality_http_disabled_mode_and_native_unavailability(quality_case, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, identities, policy = quality_case
    rule = registry.register_policy(policy)
    saved = registry.create(rule, identities[0], reference_id=identities[1])
    token = "synthetic-quality-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/historical-quality-assessments"
    rule_path = "/internal/v1/historical-quality-policies/" + rule
    body = {"policy_id": rule, "candidate_evaluation_id": identities[0]}
    with TestClient(create_app(settings)) as client:
        assert client.get(rule_path, headers=headers).status_code == 503
        assert client.get(path + "/" + saved, headers=headers).status_code == 503
        assert client.post(path, json=body, headers=headers).status_code == 503
    settings = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(
        create_app(settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        assert client.get(rule_path + "?data_mode=REPLAY", headers=headers).status_code == 404
        assert client.get(path + "/" + saved, headers=headers).status_code == 404
        assert (
            client.post(path + "?data_mode=REPLAY", json=body, headers=headers).status_code == 404
        )

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings)) as client:
        assert client.get(rule_path, headers=headers).status_code == 200
        assert client.get(path + "/" + saved, headers=headers).status_code == 503
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_quality_assessments WHERE policy_id=%s",
                (rule,),
            ).fetchone()["n"]
            == 1
        )


def test_quality_policy_catalog_scope_pagination_and_no_native_replay(quality_case, monkeypatch):
    from yta_alpha import training
    from yta_alpha.model_quality_catalog import policy_page
    from yta_alpha.models import DataMode

    registry, _, policy = quality_case
    for i in range(3):
        registry.register_policy(
            policy | {"policy_version": policy["policy_version"] + str(i), "horizon_sessions": 20}
        )
    foreign = registry.register_policy(policy | {"data_mode": "DEMO", "horizon_sessions": 20})
    other_horizon = registry.register_policy(policy)
    with registry._connect() as connection:
        expected = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM yta.model_quality_policies WHERE data_mode='REPLAY' "
                "AND horizon_sessions=20 ORDER BY created_at DESC,id DESC"
            )
        ]
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]

    def unavailable():
        raise AssertionError("Discovery must not execute native inference")

    monkeypatch.setattr(training, "library", unavailable)
    seen, cursor = [], None
    while True:
        page = policy_page(registry, DataMode.REPLAY, horizon_sessions=20, cursor=cursor, limit=2)
        for item in page.items:
            assert item.policy == registry.get_policy(item.id)
            seen.append(item.id)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == expected
    assert len(seen) == len(set(seen))
    for cursor in (foreign, other_horizon, "bad", "historical-quality-policy-" + "0" * 64):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, horizon_sessions=20, cursor=cursor)
    for limit in (0, 51, True):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, limit=limit)
    for horizon in (2, True, "5"):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, horizon_sessions=horizon)
    with registry._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_quality_policy_catalog_http(quality_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.model_quality_catalog import policy_page
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, _, policy = quality_case
    registry.register_policy(policy)
    token = "synthetic-quality-catalog-service-token-123456789"
    tool = "synthetic-quality-catalog-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.reports.calibrations.models.url),
    )
    path = "/internal/v1/historical-quality-policies"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
        result = client.get(
            path + "?horizon_sessions=5&limit=1&data_mode=LIVE_RESEARCH", headers=headers
        )
        assert result.status_code == 200
        assert result.json() == policy_page(
            registry, DataMode.REPLAY, horizon_sessions=5, limit=1
        ).model_dump(mode="json")
        for query in ("?horizon_sessions=2", "?limit=51", "?cursor=bad"):
            assert client.get(path + query, headers=headers).status_code == 422
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503


def test_model_review_preflight_binds_verified_proposal_and_quality_without_writes(quality_case):
    from yta_alpha.model_proposals import ModelProposalRegistry, ProposalRequest
    from yta_alpha.model_review import ModelReviewRequest, ModelReviewService
    from yta_alpha.models import DataMode

    quality, ids, policy = quality_case
    url, calendar = quality.reports.calibrations.models.url, quality.reports.calibrations.calendar
    proposals = ModelProposalRegistry(url, calendar)
    owner, reviewer, key = uuid4(), uuid4(), uuid4()
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_proposals TO yta_app")
        for identity in (owner, reviewer):
            connection.execute(
                "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
                (identity, str(identity) + "@review.test"),
            )
    proposal = proposals.create(
        owner,
        uuid4(),
        DataMode.REPLAY,
        ProposalRequest(
            candidate_evaluation_id=ids[0],
            reference_evaluation_id=ids[1],
            rationale="Synthetic review case",
        ),
    )
    rule = quality.register_policy(policy)
    assessment = quality.create(rule, ids[0], reference_id=ids[1])
    request = ModelReviewRequest(
        proposal_id=proposal["id"],
        assessment_id=assessment,
        action="APPROVE",
        reason="Review fixed evidence",
    )
    service = ModelReviewService(url, calendar)
    with quality._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    result = service.evaluate(reviewer, key, DataMode.REPLAY, request)
    assert result["disposition"] == "BLOCKED"
    assert result["proposal_content_hash"] == proposal["content_hash"]
    assert result["reviewer_id"] == str(reviewer)
    assert result["promotion_allowed"] is False
    assert service.evaluate(reviewer, key, DataMode.REPLAY, request) == result
    assert (
        service.evaluate(
            reviewer, key, DataMode.REPLAY, request.model_copy(update={"action": "REJECT"})
        )["disposition"]
        == "REJECTED"
    )
    with pytest.raises(DomainError) as error:
        service.evaluate(reviewer, key, DataMode.LIVE_RESEARCH, request)
    assert error.value.status == 404
    mismatched = quality.create(rule, ids[0])
    with pytest.raises(DomainError, match="exact proposal"):
        service.evaluate(
            reviewer, key, DataMode.REPLAY, request.model_copy(update={"assessment_id": mismatched})
        )
    failed_rule = quality.register_policy(
        policy | {"policy_version": policy["policy_version"] + "-strict", "minimum_samples": 1000}
    )
    failed = quality.create(failed_rule, ids[0], reference_id=ids[1])
    blocked = service.evaluate(
        reviewer, key, DataMode.REPLAY, request.model_copy(update={"assessment_id": failed})
    )
    assert "NUMERIC_GATE_FAILED" in blocked["blocking_reasons"]
    assert "minimum_samples" in blocked["failed_checks"]
    # Only the two explicit quality registrations above wrote audits, never preflight.
    with quality._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before + 3
        )


@pytest.fixture
def review_case(quality_case):
    from yta_alpha.model_proposals import ModelProposalRegistry, ProposalRequest
    from yta_alpha.model_review_registry import ModelReviewRegistry, ReviewSubmission
    from yta_alpha.models import DataMode

    quality, ids, policy = quality_case
    url, calendar = quality.reports.calibrations.models.url, quality.reports.calibrations.calendar
    people = [uuid4(), uuid4()]
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute(
            "GRANT SELECT,INSERT ON yta.model_reviews,yta.model_proposals TO yta_app"
        )
        for identity in people:
            connection.execute(
                "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
                (identity, str(identity) + "@review-record.test"),
            )
    proposals = ModelProposalRegistry(url, calendar)
    proposal = proposals.create(
        people[0],
        uuid4(),
        DataMode.REPLAY,
        ProposalRequest(
            candidate_evaluation_id=ids[0],
            reference_evaluation_id=ids[1],
            rationale="Synthetic review record",
        ),
    )
    assessment = quality.create(quality.register_policy(policy), ids[0], reference_id=ids[1])
    request = ReviewSubmission(
        proposal_id=proposal["id"],
        assessment_id=assessment,
        action="APPROVE",
        reason="Review evidence, not production qualification",
    )
    return ModelReviewRegistry(url, calendar), people, request


def test_review_records_retry_chain_and_terminal_rejection(review_case):
    from yta_alpha.models import DataMode

    registry, people, request = review_case
    key = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = list(
            pool.map(lambda _: registry.create(people[0], key, DataMode.REPLAY, request), range(4))
        )
    assert first == [first[0]] * 4
    first = first[0]
    assert first["review"]["disposition"] == "BLOCKED"
    assert first["review"]["revision"] == 1
    assert first["review"]["reviewer_id"] == str(people[0])
    with pytest.raises(DomainError) as error:
        registry.create(
            people[0], key, DataMode.REPLAY, request.model_copy(update={"reason": "changed"})
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(DomainError) as error:
        registry.create(people[1], uuid4(), DataMode.REPLAY, request)
    assert error.value.code == "MODEL_REVIEW_STALE"
    rejected = registry.create(
        people[1],
        uuid4(),
        DataMode.REPLAY,
        request.model_copy(
            update={"action": "REJECT", "expected_previous_review_id": UUID(first["id"])}
        ),
    )
    assert rejected["review"]["revision"] == 2
    assert rejected["review"]["disposition"] == "REJECTED"
    assert registry.create(people[0], key, DataMode.REPLAY, request) == first
    assert registry.get(rejected["id"], DataMode.REPLAY) == rejected
    with pytest.raises(DomainError) as error:
        registry.create(
            people[0],
            uuid4(),
            DataMode.REPLAY,
            request.model_copy(update={"expected_previous_review_id": UUID(rejected["id"])}),
        )
    assert error.value.code == "MODEL_REVIEW_CLOSED"
    with pytest.raises(DomainError) as error:
        registry.get(first["id"], DataMode.LIVE_RESEARCH)
    assert error.value.status == 404
    with registry._connect() as connection:
        for saved in (first, rejected):
            audit = connection.execute(
                "SELECT actor_id FROM yta.audit_events WHERE action='model.review_recorded' "
                "AND resource_id=%s",
                (saved["id"],),
            ).fetchall()
            assert [str(row["actor_id"]) for row in audit] == [saved["review"]["reviewer_id"]]


def test_review_concurrent_distinct_reviewers_cannot_fork(review_case):
    from yta_alpha.models import DataMode

    registry, people, request = review_case

    def attempt(person):
        try:
            return registry.create(person, uuid4(), DataMode.REPLAY, request)
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, people))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert results.count("MODEL_REVIEW_STALE") == 1


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_review_records_immutable_for_owner_and_runtime(review_case, verb):
    from yta_alpha.models import DataMode

    registry, people, request = review_case
    registry.create(people[0], uuid4(), DataMode.REPLAY, request)
    statement = {
        "UPDATE": "UPDATE yta.model_reviews SET payload=payload",
        "DELETE": "DELETE FROM yta.model_reviews",
        "TRUNCATE": "TRUNCATE yta.model_reviews CASCADE",
    }[verb]
    for url in (
        os.environ["YTA_TEST_DATABASE_URL"],
        registry.service.proposals.reports.calibrations.models.url,
    ):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
            connection.rollback()


def test_review_audit_failure_rolls_back_fact(review_case, monkeypatch):
    from yta_alpha.models import DataMode

    registry, people, request = review_case
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected review audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected review audit failure"):
        registry.create(people[0], uuid4(), DataMode.REPLAY, request)
    with original() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.model_reviews WHERE proposal_id=%s", (request.proposal_id,)
            ).fetchone()
            is None
        )


def test_review_record_replay_rejects_rehashed_promotion_and_revision_tampering(review_case):
    from copy import deepcopy

    from yta_alpha.model_review_registry import verify_review
    from yta_alpha.models import DataMode

    registry, people, request = review_case
    saved = registry.create(people[0], uuid4(), DataMode.REPLAY, request)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.model_reviews WHERE id=%s", (saved["id"],)
        ).fetchone()
    proposal = registry.service.proposals.get(people[0], DataMode.REPLAY, request.proposal_id)
    assessment = registry.service.quality.get(request.assessment_id, data_mode=DataMode.REPLAY)
    for changes in ({"promotion_allowed": True}, {"disposition": "APPROVED"}, {"revision": 2}):
        changed = deepcopy(row)
        changed["payload"].update(changes)
        changed["payload"].pop("content_hash")
        digest = content_hash(changed["payload"])
        changed["payload"]["content_hash"] = digest
        changed["content_hash"] = digest
        if "revision" in changes:
            changed["revision"] = 2
        with pytest.raises(DomainError, match="chain or evidence"):
            verify_review(changed, proposal, assessment, None)


@pytest.fixture(params=["review_case", "combined_review_registry_case"])
def review_http_case(request):
    case = request.getfixturevalue(request.param)
    if request.param == "combined_review_registry_case":
        registry, owner, other, submission = case
        return registry, (owner, other), submission
    return case


def test_review_http_auth_binding_retry_and_state_conflicts(review_http_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr, ValidationError
    from yta_alpha.api import create_app
    from yta_alpha.model_review_contracts import ModelReviewResponse
    from yta_alpha.settings import Settings

    registry, people, request = review_http_case
    token, tool = (
        "synthetic-review-service-token-123456789",
        "synthetic-review-tools-token-123456789",
    )
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.service.proposals.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-reviews"
    headers = {
        "Authorization": "Bearer " + token,
        "X-YTA-Owner-ID": str(people[0]),
        "Idempotency-Key": str(uuid4()),
    }
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path, json=body, headers=headers | {"Authorization": "Bearer " + tool}
            ).status_code
            == 401
        )
        for field in ("X-YTA-Owner-ID", "Idempotency-Key"):
            assert (
                client.post(
                    path,
                    json=body,
                    headers={key: value for key, value in headers.items() if key != field},
                ).status_code
                == 422
            )
            assert client.post(path, json=body, headers=headers | {field: "bad"}).status_code == 422
        for extra in (
            {"reviewer_id": str(people[1])},
            {"disposition": "APPROVED"},
            {"data_mode": "LIVE_RESEARCH"},
            {"promotion_allowed": True},
        ):
            assert client.post(path, json=body | extra, headers=headers).status_code == 422
        conflicting = (
            {"assessment_id": "quality-assessment-" + "0" * 64}
            if "quality_gate_id" in body
            else {"quality_gate_id": "model-quality-gate-" + "0" * 64}
        )
        assert client.post(path, json=body | conflicting, headers=headers).status_code == 422
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 200
        first = created.json()
        assert first["review"]["reviewer_id"] == str(people[0])
        assert first["review"]["disposition"] == "BLOCKED"
        assert ModelReviewResponse.model_validate(first).model_dump(mode="json") == first
        assert client.post(path, json=body, headers=headers).json() == first
        assert client.get(path + "/" + first["id"], headers=headers).json() == first
        assert client.get(path + "/" + first["id"]).status_code == 401
        assert client.get(path + "/" + str(uuid4()), headers=headers).status_code == 404
        assert (
            client.post(path, json=body | {"reason": "changed"}, headers=headers).status_code == 409
        )
        newer = headers | {"Idempotency-Key": str(uuid4()), "X-YTA-Owner-ID": str(people[1])}
        assert (
            client.post(path, json=body, headers=newer).json()["error"]["code"]
            == "MODEL_REVIEW_STALE"
        )
        reject = body | {"action": "REJECT", "expected_previous_review_id": first["id"]}
        response = client.post(path, json=reject, headers=newer)
        assert response.status_code == 200
        second = response.json()
        assert second["review"]["revision"] == 2
        assert second["review"]["disposition"] == "REJECTED"
        assert client.post(path, json=body, headers=headers).json() == first
        assert client.post(path, json=reject, headers=newer).json() == second
        closed = client.post(
            path,
            json=body | {"expected_previous_review_id": second["id"]},
            headers=headers | {"Idempotency-Key": str(uuid4())},
        )
        assert closed.status_code == 409
        assert closed.json()["error"]["code"] == "MODEL_REVIEW_CLOSED"
        with pytest.raises(ValidationError):
            ModelReviewResponse.model_validate(
                first | {"review": first["review"] | {"disposition": "APPROVED"}}
            )


def test_review_http_disabled_mode_and_native_failure(review_http_case, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.model_review_registry import CombinedReviewSubmission
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, people, request = review_http_case
    key = uuid4()
    create = (
        registry.create_combined
        if isinstance(request, CombinedReviewSubmission)
        else registry.create
    )
    saved = create(people[0], key, DataMode.REPLAY, request)
    token = "synthetic-review-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.service.proposals.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-reviews"
    headers = {
        "Authorization": "Bearer " + token,
        "X-YTA-Owner-ID": str(people[0]),
        "Idempotency-Key": str(key),
    }
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.post(path, json=body, headers=headers).status_code == 503
        assert client.get(path + "/" + saved["id"], headers=headers).status_code == 503
    settings = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(
        create_app(settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        assert (
            client.post(path + "?data_mode=REPLAY", json=body, headers=headers).status_code == 404
        )
        assert client.get(path + "/" + saved["id"], headers=headers).status_code == 404

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native evidence replay unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings)) as client:
        assert client.get(path + "/" + saved["id"], headers=headers).status_code == 503
        assert client.post(path, json=body, headers=headers).status_code == 503
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_reviews WHERE proposal_id=%s",
                (request.proposal_id,),
            ).fetchone()["n"]
            == 1
        )


def test_review_history_paging_append_and_scope(review_case):
    from yta_alpha.models import DataMode

    registry, people, request = review_case
    mode = DataMode.REPLAY
    assert registry.history(request.proposal_id, mode) == {"items": [], "next_cursor": None}
    first = registry.create(people[0], uuid4(), mode, request)
    second = registry.create(
        people[1],
        uuid4(),
        mode,
        request.model_copy(update={"expected_previous_review_id": UUID(first["id"])}),
    )
    page = registry.history(request.proposal_id, mode, limit=1)
    assert page == {"items": [second], "next_cursor": second["id"]}
    third = registry.create(
        people[0],
        uuid4(),
        mode,
        request.model_copy(
            update={"expected_previous_review_id": UUID(second["id"]), "action": "REJECT"}
        ),
    )
    # Appends do not shift an existing cursor or repeat its record.
    assert registry.history(request.proposal_id, mode, cursor=page["next_cursor"], limit=1) == {
        "items": [first],
        "next_cursor": None,
    }
    assert registry.history(request.proposal_id, mode) == {
        "items": [third, second, first],
        "next_cursor": None,
    }
    assert registry.history(request.proposal_id, mode, cursor=first["id"]) == {
        "items": [],
        "next_cursor": None,
    }
    with pytest.raises(DomainError, match="outside proposal scope"):
        registry.history(request.proposal_id, mode, cursor=uuid4())
    with pytest.raises(DomainError) as error:
        registry.history(request.proposal_id, DataMode.LIVE_RESEARCH)
    assert error.value.status == 404
    for limit in [0, 51, True]:
        with pytest.raises(DomainError, match="Limit"):
            registry.history(request.proposal_id, mode, limit=limit)
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE "
                "action='model.review_recorded' AND payload->>'proposal_id'=%s",
                (str(request.proposal_id),),
            ).fetchone()["n"]
            == 3
        )


def test_review_history_http_auth_and_validation(review_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, people, request = review_case
    token = "synthetic-review-history-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.service.proposals.reports.calibrations.models.url),
    )
    path = f"/internal/v1/model-proposals/{request.proposal_id}/reviews"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(
        create_app(settings, calendar=registry.service.proposals.reports.calibrations.calendar)
    ) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).json() == {"items": [], "next_cursor": None}
        saved = registry.create(people[0], uuid4(), DataMode.REPLAY, request)
        assert client.get(path, headers=headers).json() == {"items": [saved], "next_cursor": None}
        for query in ["limit=0", "limit=51", "cursor=bad"]:
            assert client.get(path + "?" + query, headers=headers).status_code == 422
        assert client.get(path + "?cursor=" + str(uuid4()), headers=headers).status_code == 422
        assert (
            client.get(
                f"/internal/v1/model-proposals/{uuid4()}/reviews", headers=headers
            ).status_code
            == 404
        )


def test_reviewer_proposal_discovery_preserves_owner_scope(review_case):
    from yta_alpha.model_proposal_catalog import (
        proposal_page,
        review_proposal,
        review_proposal_page,
    )
    from yta_alpha.model_proposals import ProposalRequest
    from yta_alpha.models import DataMode

    reviews, people, request = review_case
    registry = reviews.service.proposals
    mode = DataMode.REPLAY
    first = registry.get(people[0], mode, request.proposal_id)
    second = registry.create(
        people[1],
        uuid4(),
        mode,
        ProposalRequest(
            candidate_evaluation_id=first["proposal"]["candidate_evaluation_id"],
            reference_evaluation_id=first["proposal"]["reference_evaluation_id"],
            rationale="Other owner",
        ),
    )
    page = review_proposal_page(registry, mode, limit=1)
    assert page.items[0].id == UUID(second["id"])
    assert page.next_cursor == UUID(second["id"])
    older = review_proposal_page(registry, mode, cursor=page.next_cursor, limit=1)
    assert older.items[0].id == UUID(first["id"])
    assert review_proposal(registry, mode, second["id"]) == second
    assert [str(item.id) for item in proposal_page(registry, people[0], mode).items] == [
        first["id"]
    ]
    with pytest.raises(DomainError):
        registry.get(people[0], mode, second["id"])
    with pytest.raises(DomainError) as error:
        review_proposal(registry, DataMode.LIVE_RESEARCH, second["id"])
    assert error.value.status == 404
    with pytest.raises(DomainError, match="outside proposal scope"):
        review_proposal_page(registry, DataMode.LIVE_RESEARCH, cursor=second["id"])
    for limit in [0, 51, True]:
        with pytest.raises(DomainError):
            review_proposal_page(registry, mode, limit=limit)


def test_reviewer_proposal_api_auth_mode_and_disabled(review_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, people, request = review_case
    token = "synthetic-review-proposals-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        tools_token=SecretStr("synthetic-tools-token-123456789"),
        database_url=SecretStr(registry.service.proposals.reports.calibrations.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/model-review-proposals"
    calendar = registry.service.proposals.reports.calibrations.calendar
    with TestClient(create_app(settings, calendar=calendar)) as client:
        for endpoint in [path, path + "/" + str(request.proposal_id)]:
            assert client.get(endpoint).status_code == 401
            assert (
                client.get(
                    endpoint, headers={"Authorization": "Bearer synthetic-tools-token-123456789"}
                ).status_code
                == 401
            )
        page = client.get(path + "?limit=1", headers=headers)
        assert page.status_code == 200
        assert page.json()["items"][0]["id"] == str(request.proposal_id)
        expected = registry.service.proposals.get(people[0], DataMode.REPLAY, request.proposal_id)
        assert client.get(path + "/" + str(request.proposal_id), headers=headers).json() == expected
        assert client.get(path + "?limit=51", headers=headers).status_code == 422
        assert client.get(path + "?cursor=" + str(uuid4()), headers=headers).status_code == 422
        assert client.get(path + "/" + str(uuid4()), headers=headers).status_code == 404
    with TestClient(
        create_app(
            settings.model_copy(update={"experimental_models": "DISABLED"}), calendar=calendar
        )
    ) as client:
        assert client.get(path, headers=headers).status_code == 503
    with TestClient(
        create_app(
            settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), calendar=calendar
        )
    ) as client:
        assert client.get(path + "/" + str(request.proposal_id), headers=headers).status_code == 404


def test_calibration_report_projection_and_strict_identity(setup):
    from copy import deepcopy

    from pydantic import ValidationError
    from yta_alpha.calibration_contracts import CalibrationReportResponse
    from yta_alpha.calibration_reports import calibration_report
    from yta_alpha.models import DataMode

    registry, model_id, spec, _ = setup
    identity = registry.create(model_id, spec)
    payload = registry.get(identity)
    result = calibration_report(registry, identity, DataMode.REPLAY).model_dump(mode="json")
    assert result["report"] == payload["report"]
    assert result["content_hash"] == content_hash(payload)
    assert result["report_hash"] == content_hash(payload["report"])
    assert set(result) == {
        "id",
        "model_id",
        "registered_at",
        "content_hash",
        "report_hash",
        "report",
    }
    assert result["report"]["qualification"] == "UNVERIFIED"
    for mutate in [
        lambda r: r["report"].update(qualification="APPROVED"),
        lambda r: r["report"]["probabilities"].__setitem__(0, "1.1"),
        lambda r: r["report"]["reliability"].update(sample_count=0),
        lambda r: r["report"].update(evaluation_ids=r["report"]["calibration_ids"]),
        lambda r: r.update(id="calibration-experimental-" + "0" * 64),
    ]:
        changed = deepcopy(result)
        mutate(changed)
        changed["report_hash"] = content_hash(changed["report"])
        with pytest.raises(ValidationError):
            CalibrationReportResponse.model_validate(changed)
    with pytest.raises(DomainError) as error:
        calibration_report(registry, identity, DataMode.LIVE_RESEARCH)
    assert error.value.status == 404


def test_calibration_report_http_auth_mode_disabled_and_native_failure(setup, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, model_id, spec, _ = setup
    identity = registry.create(model_id, spec)
    token = "synthetic-calibration-report-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        tools_token=SecretStr("synthetic-tools-token-123456789"),
        database_url=SecretStr(registry.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/experimental-calibrations/" + identity
    with TestClient(create_app(settings, calendar=registry.calendar)) as client:
        assert client.get(path).status_code == 401
        assert (
            client.get(
                path, headers={"Authorization": "Bearer synthetic-tools-token-123456789"}
            ).status_code
            == 401
        )
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["report"] == registry.get(identity)["report"]
        assert (
            client.get("/internal/v1/experimental-calibrations/bad", headers=headers).status_code
            == 422
        )
    with TestClient(
        create_app(
            settings.model_copy(update={"experimental_models": "DISABLED"}),
            calendar=registry.calendar,
        )
    ) as client:
        assert client.get(path, headers=headers).status_code == 503
    with TestClient(
        create_app(
            settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}),
            calendar=registry.calendar,
        )
    ) as client:
        assert client.get(path, headers=headers).status_code == 404

    def unavailable(*args, **kwargs):
        raise DomainError("MODEL_UNAVAILABLE", "Native inference unavailable", status=503)

    monkeypatch.setattr("yta_alpha.calibration_registry.rebuild_report", unavailable)
    with TestClient(create_app(settings, calendar=registry.calendar)) as client:
        assert client.get(path, headers=headers).status_code == 503


def test_probability_quality_uses_full_registry_replay_and_does_not_write(setup):
    from yta_alpha.models import DataMode
    from yta_alpha.probability_quality import ProbabilityQualityPolicy

    registry, model_id, spec, _ = setup
    identity = registry.create(model_id, spec)
    rule = ProbabilityQualityPolicy(
        policy_version="synthetic-probability-test-v1",
        data_mode="REPLAY",
        horizon_sessions=5,
        minimum_calibration_samples=1,
        minimum_evaluation_samples=1,
        minimum_evaluation_up=1,
        minimum_evaluation_non_up=1,
        maximum_brier="1",
        maximum_ece="1",
    )
    with registry.models._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    first = registry.assess_quality(identity, rule)
    assert first["calibration_id"] == identity
    assert first["model_id"] == model_id
    assert first["numeric_pass"] is True
    assert first["promotion_allowed"] is False
    assert registry.assess_quality(identity, rule) == first
    stricter = rule.model_copy(update={"minimum_evaluation_up": 1000})
    assert registry.assess_quality(identity, stricter)["numeric_pass"] is False
    with pytest.raises(DomainError) as error:
        registry.assess_quality(
            identity, rule.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
        )
    assert error.value.status == 404
    with registry.models._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


@pytest.fixture
def probability_case(setup):
    from yta_alpha.probability_quality_registry import ProbabilityQualityRegistry

    calibrations, model_id, spec, _ = setup
    identity = calibrations.create(model_id, spec)
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute(
            "GRANT SELECT,INSERT ON yta.probability_quality_policies,"
            "yta.probability_quality_assessments TO yta_app"
        )
    policy = {
        "policy_version": "synthetic-probability-" + uuid4().hex,
        "data_mode": "REPLAY",
        "horizon_sessions": 5,
        "minimum_calibration_samples": 1,
        "minimum_evaluation_samples": 1,
        "minimum_evaluation_up": 1,
        "minimum_evaluation_non_up": 1,
        "maximum_brier": "1",
        "maximum_ece": "1",
    }
    return (
        ProbabilityQualityRegistry(calibrations.models.url, calibrations.calendar),
        identity,
        policy,
    )


def test_probability_registry_concurrent_rules_assessments_and_replay(probability_case):
    from yta_alpha.models import DataMode

    registry, identity, policy = probability_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        rules = list(pool.map(lambda _: registry.register_policy(policy), range(4)))
    assert len(set(rules)) == 1
    rule = rules[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: registry.create(rule, identity), range(4)))
    assert len(set(results)) == 1
    result = registry.get(results[0], data_mode=DataMode.REPLAY)
    assert result == registry.calibrations.assess_quality(identity, policy)
    assert result["promotion_allowed"] is False
    with pytest.raises(DomainError) as error:
        registry.register_policy(policy | {"maximum_brier": "0.5"})
    assert error.value.status == 409
    with pytest.raises(DomainError):
        registry.get(results[0], data_mode=DataMode.LIVE_RESEARCH)
    with pytest.raises(DomainError):
        registry.get_policy(rule, data_mode=DataMode.LIVE_RESEARCH)
    strict = registry.register_policy(
        policy
        | {
            "policy_version": policy["policy_version"] + "-strict",
            "minimum_evaluation_samples": 1000,
        }
    )
    missing = registry.create(strict, identity)
    assert registry.get(missing)["numeric_pass"] is False
    with registry._connect() as connection:
        for identity in (rule, results[0], missing):
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
                ).fetchone()["n"]
                == 1
            )


@pytest.mark.parametrize(
    "table", ["probability_quality_policies", "probability_quality_assessments"]
)
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_probability_registry_facts_are_immutable_for_owner_and_runtime(
    probability_case, table, verb
):
    from psycopg import sql

    registry, identity, policy = probability_case
    rule = registry.register_policy(policy)
    registry.create(rule, identity)
    statement = {
        "UPDATE": "UPDATE yta.{} SET payload=payload",
        "DELETE": "DELETE FROM yta.{}",
        "TRUNCATE": "TRUNCATE yta.{} CASCADE",
    }[verb]
    for url in (os.environ["YTA_TEST_DATABASE_URL"], registry.calibrations.models.url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(sql.SQL(statement).format(sql.Identifier(table)))
            connection.rollback()


@pytest.mark.parametrize("kind", ["policy", "assessment"])
def test_probability_registry_audit_failure_rolls_back(probability_case, monkeypatch, kind):
    registry, identity, policy = probability_case
    rule = registry.register_policy(policy) if kind == "assessment" else None
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected quality audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected quality audit failure"):
        if kind == "policy":
            registry.register_policy(policy)
        else:
            registry.create(rule, identity)
    with original() as connection:
        if kind == "policy":
            assert (
                connection.execute(
                    "SELECT id FROM yta.probability_quality_policies WHERE policy_version=%s",
                    (policy["policy_version"],),
                ).fetchone()
                is None
            )
        else:
            assert (
                connection.execute(
                    "SELECT id FROM yta.probability_quality_assessments WHERE policy_id=%s", (rule,)
                ).fetchone()
                is None
            )


def test_probability_registry_rehashed_tampering_cannot_grant_approval(probability_case):
    from copy import deepcopy

    from yta_alpha.probability_quality_registry import ASSESSMENT_PREFIX, verify_assessment

    registry, identity, policy = probability_case
    rule = registry.register_policy(policy)
    assessment_id = registry.create(rule, identity)
    from yta_alpha.calibration_reports import calibration_report
    from yta_alpha.models import DataMode

    report = calibration_report(registry.calibrations, identity, DataMode.REPLAY).model_dump(
        mode="json"
    )
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.probability_quality_assessments WHERE id=%s", (assessment_id,)
        ).fetchone()
    for field, value in [
        ("promotion_allowed", True),
        ("numeric_pass", not row["payload"]["numeric_pass"]),
        ("qualification", "VERIFIED"),
    ]:
        modified = deepcopy(row)
        modified["payload"][field] = value
        modified["payload"].pop("content_hash")
        digest = content_hash(modified["payload"])
        modified.update(id=ASSESSMENT_PREFIX + digest, content_hash=digest)
        modified["payload"]["content_hash"] = digest
        with pytest.raises(DomainError, match="reproduced"):
            verify_assessment(modified, registry.get_policy(rule), report)


def test_probability_quality_http_auth_exact_registration_and_contracts(probability_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr, ValidationError
    from yta_alpha.api import create_app
    from yta_alpha.probability_quality_contracts import ProbabilityAssessmentResponse
    from yta_alpha.settings import Settings

    registry, calibration_id, policy = probability_case
    rule = registry.register_policy(policy)
    token = "synthetic-quality-service-token-123456789"
    tool = "synthetic-quality-tools-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/probability-quality-assessments"
    rule_path = "/internal/v1/probability-quality-policies/" + rule
    body = {
        "policy_id": rule,
        "calibration_id": calibration_id,
    }
    with TestClient(create_app(settings)) as client:
        for auth_headers in ({}, {"Authorization": "Bearer " + tool}):
            assert client.post(path, json=body, headers=auth_headers).status_code == 401
            assert client.get(rule_path, headers=auth_headers).status_code == 401
        for injected in (
            {"data_mode": "LIVE_RESEARCH"},
            {"numeric_pass": True},
            {"policy": policy},
            {"promotion_allowed": True},
        ):
            assert client.post(path, json=body | injected, headers=headers).status_code == 422
        assert client.get(rule_path + "invalid", headers=headers).status_code == 422
        assert client.get(path + "/invalid", headers=headers).status_code == 422
        rule_response = client.get(rule_path, headers=headers)
        assert rule_response.status_code == 200
        assert rule_response.json()["policy"] == registry.get_policy(rule).model_dump(mode="json")
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 200
        saved = created.json()
        identity = "probability-quality-assessment-" + saved["content_hash"]
        assert saved == registry.get(identity)
        assert saved["promotion_allowed"] is False
        assert client.post(path, json=body, headers=headers).json() == saved
        assert client.get(path + "/" + identity, headers=headers).json() == saved
        assert (
            client.get(
                path + "/probability-quality-assessment-" + "0" * 64, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/internal/v1/probability-quality-policies/probability-quality-policy-" + "0" * 64,
                headers=headers,
            ).status_code
            == 404
        )
        assert ProbabilityAssessmentResponse.model_validate(saved).model_dump(mode="json") == saved
        with pytest.raises(ValidationError):
            ProbabilityAssessmentResponse.model_validate(
                saved | {"numeric_pass": not saved["numeric_pass"]}
            )
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_probability_quality_http_disabled_mode_and_native_unavailability(
    probability_case, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, calibration_id, policy = probability_case
    rule = registry.register_policy(policy)
    saved = registry.create(rule, calibration_id)
    token = "synthetic-quality-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    headers = {"Authorization": "Bearer " + token}
    path = "/internal/v1/probability-quality-assessments"
    rule_path = "/internal/v1/probability-quality-policies/" + rule
    body = {"policy_id": rule, "calibration_id": calibration_id}
    with TestClient(create_app(settings)) as client:
        assert client.get(rule_path, headers=headers).status_code == 503
        assert client.get(path + "/" + saved, headers=headers).status_code == 503
        assert client.post(path, json=body, headers=headers).status_code == 503
    settings = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(
        create_app(settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        assert client.get(rule_path + "?data_mode=REPLAY", headers=headers).status_code == 404
        assert client.get(path + "/" + saved, headers=headers).status_code == 404
        assert (
            client.post(path + "?data_mode=REPLAY", json=body, headers=headers).status_code == 404
        )

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(settings)) as client:
        assert client.get(rule_path, headers=headers).status_code == 200
        assert client.get(path + "/" + saved, headers=headers).status_code == 503
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.probability_quality_assessments WHERE policy_id=%s",
                (rule,),
            ).fetchone()["n"]
            == 1
        )


def test_probability_policy_catalog_scope_pagination_and_no_native_replay(
    probability_case, monkeypatch
):
    from yta_alpha import training
    from yta_alpha.models import DataMode
    from yta_alpha.probability_quality_catalog import policy_page

    registry, _, policy = probability_case
    for i in range(3):
        registry.register_policy(
            policy | {"policy_version": policy["policy_version"] + str(i), "horizon_sessions": 20}
        )
    foreign = registry.register_policy(policy | {"data_mode": "DEMO", "horizon_sessions": 20})
    other_horizon = registry.register_policy(policy)
    with registry._connect() as connection:
        expected = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM yta.probability_quality_policies WHERE data_mode='REPLAY' "
                "AND horizon_sessions=20 ORDER BY created_at DESC,id DESC"
            )
        ]
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]

    def unavailable():
        raise AssertionError("Discovery must not execute native inference")

    monkeypatch.setattr(training, "library", unavailable)
    seen, cursor = [], None
    while True:
        page = policy_page(registry, DataMode.REPLAY, horizon_sessions=20, cursor=cursor, limit=2)
        for item in page.items:
            assert item.policy == registry.get_policy(item.id)
            seen.append(item.id)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == expected
    assert len(seen) == len(set(seen))
    for cursor in (foreign, other_horizon, "bad", "probability-quality-policy-" + "0" * 64):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, horizon_sessions=20, cursor=cursor)
    for limit in (0, 51, True):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, limit=limit)
    for horizon in (2, True, "5"):
        with pytest.raises(DomainError):
            policy_page(registry, DataMode.REPLAY, horizon_sessions=horizon)
    with registry._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_probability_policy_catalog_http(probability_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.probability_quality_catalog import policy_page
    from yta_alpha.settings import Settings

    registry, _, policy = probability_case
    registry.register_policy(policy)
    token = "synthetic-quality-catalog-service-token-123456789"
    tool = "synthetic-quality-catalog-tool-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.calibrations.models.url),
    )
    path = "/internal/v1/probability-quality-policies"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
        result = client.get(
            path + "?horizon_sessions=5&limit=1&data_mode=LIVE_RESEARCH", headers=headers
        )
        assert result.status_code == 200
        assert result.json() == policy_page(
            registry, DataMode.REPLAY, horizon_sessions=5, limit=1
        ).model_dump(mode="json")
        for query in ("?horizon_sessions=2", "?limit=51", "?cursor=bad"):
            assert client.get(path + query, headers=headers).status_code == 422
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503


@pytest.fixture
def quality_gate_case(quality_case):
    from yta_alpha.model_proposals import ModelProposalRegistry, ProposalRequest
    from yta_alpha.model_quality_gate import ModelQualityGateRequest, ModelQualityGateService
    from yta_alpha.models import DataMode

    price, ids, policy = quality_case
    url, calendar = price.reports.calibrations.models.url, price.reports.calibrations.calendar
    service = ModelQualityGateService(url, calendar)
    owner = uuid4()
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute(
            "GRANT SELECT,INSERT ON yta.model_proposals,yta.probability_quality_policies,"
            "yta.probability_quality_assessments TO yta_app"
        )
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES(%s,%s)", (owner, str(owner) + "@gate.test")
        )
    proposal = ModelProposalRegistry(url, calendar).create(
        owner,
        uuid4(),
        DataMode.REPLAY,
        ProposalRequest(
            candidate_evaluation_id=ids[0],
            reference_evaluation_id=ids[1],
            rationale="Synthetic combined quality case",
        ),
    )
    rule = price.register_policy(policy)
    price_id = price.create(rule, ids[0], reference_id=ids[1])
    probability_rule = service.probability.register_policy(
        {
            "policy_version": "synthetic-combined-" + uuid4().hex,
            "data_mode": "REPLAY",
            "horizon_sessions": 5,
            "minimum_calibration_samples": 1,
            "minimum_evaluation_samples": 1,
            "minimum_evaluation_up": 1,
            "minimum_evaluation_non_up": 1,
            "maximum_brier": "1",
            "maximum_ece": "1",
        }
    )
    candidate = price.reports.get(ids[0])
    probability_id = service.probability.create(probability_rule, candidate["calibration_id"])
    request = ModelQualityGateRequest(
        proposal_id=proposal["id"],
        price_assessment_id=price_id,
        probability_assessment_id=probability_id,
    )
    return service, owner, request, proposal, candidate


def test_quality_gate_binds_replayed_evidence_without_writes(quality_gate_case, monkeypatch):
    from yta_alpha import training
    from yta_alpha.models import DataMode

    service, owner, request, proposal, candidate = quality_gate_case
    with service.price._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    result = service.evaluate(owner, DataMode.REPLAY, request)
    assert result == service.evaluate(owner, DataMode.REPLAY, request)
    assert result["proposal_content_hash"] == proposal["content_hash"]
    assert result["calibration_id"] == candidate["calibration_id"]
    assert result["promotion_allowed"] is False
    assert result["qualification"] == "UNVERIFIED"
    assert result["numeric_pass"] == (
        result["price_numeric_pass"] and result["probability_numeric_pass"]
    )
    assert "HISTORICAL_EVIDENCE_UNQUALIFIED" in result["promotion_blockers"]
    assert "HUMAN_APPROVAL_REQUIRED" in result["promotion_blockers"]
    assert result.pop("content_hash") == content_hash(result)
    for user, mode in [(uuid4(), DataMode.REPLAY), (owner, DataMode.LIVE_RESEARCH)]:
        with pytest.raises(DomainError) as error:
            service.evaluate(user, mode, request)
        assert error.value.status == 404

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        service.evaluate(owner, DataMode.REPLAY, request)
    assert error.value.status == 503
    with service.price._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_quality_gate_rejects_other_registered_probability_or_price_scope(quality_gate_case):
    from yta_alpha.models import DataMode

    service, owner, request, proposal, candidate = quality_gate_case
    probability = service.probability.get(request.probability_assessment_id)
    reference = service.price.reports.get(proposal["proposal"]["reference_evaluation_id"])
    other = service.probability.create(
        "probability-quality-policy-" + probability["policy_hash"], reference["calibration_id"]
    )
    with pytest.raises(DomainError, match="exact proposal"):
        service.evaluate(
            owner, DataMode.REPLAY, request.model_copy(update={"probability_assessment_id": other})
        )
    price = service.price.get(request.price_assessment_id)
    missing_reference = service.price.create(
        "historical-quality-policy-" + price["policy_hash"], price["candidate_evaluation_id"]
    )
    with pytest.raises(DomainError, match="exact proposal"):
        service.evaluate(
            owner,
            DataMode.REPLAY,
            request.model_copy(update={"price_assessment_id": missing_reference}),
        )


def test_quality_gate_keeps_both_numeric_failures_and_fixed_policy_versions(quality_gate_case):
    from yta_alpha.models import DataMode

    service, owner, request, proposal, candidate = quality_gate_case
    price = service.price.get(request.price_assessment_id)
    probability = service.probability.get(request.probability_assessment_id)
    price_rule = service.price.register_policy(
        price["policy"]
        | {"policy_version": price["policy"]["policy_version"] + "-strict", "minimum_samples": 1000}
    )
    probability_rule = service.probability.register_policy(
        probability["policy"]
        | {
            "policy_version": probability["policy"]["policy_version"] + "-strict",
            "minimum_evaluation_samples": 1000,
        }
    )
    price_id = service.price.create(
        price_rule, price["candidate_evaluation_id"], reference_id=price["reference_evaluation_id"]
    )
    probability_id = service.probability.create(probability_rule, candidate["calibration_id"])
    result = service.evaluate(
        owner,
        DataMode.REPLAY,
        request.model_copy(
            update={"price_assessment_id": price_id, "probability_assessment_id": probability_id}
        ),
    )
    assert result["numeric_pass"] is False
    assert "minimum_samples" in result["failed_price_checks"]
    assert "minimum_evaluation_samples" in result["failed_probability_checks"]
    assert "PRICE_NUMERIC_GATE_FAILED" in result["promotion_blockers"]
    assert "PROBABILITY_NUMERIC_GATE_FAILED" in result["promotion_blockers"]
    assert result["price_policy_hash"] == price_rule.removeprefix("historical-quality-policy-")
    assert result["probability_policy_hash"] == probability_rule.removeprefix(
        "probability-quality-policy-"
    )


def test_quality_gate_rehashed_substitutions_and_authority_injection(quality_gate_case):
    from copy import deepcopy

    from pydantic import ValidationError
    from yta_alpha.model_quality_gate import ModelQualityGateRequest, combine_quality
    from yta_alpha.models import DataMode

    service, owner, request, proposal, candidate = quality_gate_case
    price = service.price.get(request.price_assessment_id)
    probability = service.probability.get(request.probability_assessment_id)
    for field, value in [
        ("model_id", "lightgbm-experimental-" + "0" * 64),
        ("calibration_id", "calibration-experimental-" + "0" * 64),
        ("calibration_content_hash", "0" * 64),
        ("evaluation_cohort_hash", "0" * 64),
    ]:
        altered = deepcopy(probability)
        altered[field] = value
        altered.pop("content_hash")
        altered["content_hash"] = content_hash(altered)
        changed = request.model_copy(
            update={
                "probability_assessment_id": "probability-quality-assessment-"
                + altered["content_hash"]
            }
        )
        with pytest.raises(DomainError, match="exact proposal"):
            combine_quality(DataMode.REPLAY, changed, proposal, candidate, price, altered)
    for extra in (
        {"owner_id": str(owner)},
        {"data_mode": "LIVE_RESEARCH"},
        {"promotion_allowed": True},
        {"numeric_pass": True},
    ):
        with pytest.raises(ValidationError):
            ModelQualityGateRequest.model_validate(request.model_dump(mode="json") | extra)


@pytest.fixture
def quality_gate_registry_case(quality_gate_case):
    from yta_alpha.model_quality_gate_registry import ModelQualityGateRegistry

    service, owner, request, _, _ = quality_gate_case
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_quality_gates TO yta_app")
    registry = ModelQualityGateRegistry(
        service.price.reports.calibrations.models.url, service.price.reports.calibrations.calendar
    )
    return registry, owner, request


def test_quality_gate_registry_concurrency_owner_scope_and_audit(quality_gate_registry_case):
    from yta_alpha.models import DataMode

    registry, owner, request = quality_gate_registry_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(
            pool.map(lambda _: registry.create(owner, DataMode.REPLAY, request), range(4))
        )
    assert len(set(identities)) == 1
    identity = identities[0]
    expected = registry.service.evaluate(owner, DataMode.REPLAY, request)
    assert registry.get(owner, DataMode.REPLAY, identity) == expected
    assert registry.create(owner, DataMode.REPLAY, request) == identity
    for user, mode in [(uuid4(), DataMode.REPLAY), (owner, DataMode.LIVE_RESEARCH)]:
        for action in (
            lambda user=user, mode=mode: registry.get(user, mode, identity),
            lambda user=user, mode=mode: registry.create(user, mode, request),
        ):
            with pytest.raises(DomainError) as error:
                action()
            assert error.value.status == 404
    with registry._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM yta.model_quality_gates WHERE id=%s", (identity,)
        ).fetchall()
        audits = connection.execute(
            "SELECT actor_id,payload FROM yta.audit_events WHERE resource_id=%s", (identity,)
        ).fetchall()
    assert len(rows) == len(audits) == 1
    assert rows[0]["payload"] == expected
    assert audits[0]["actor_id"] == owner
    assert audits[0]["payload"]["promotion_allowed"] is False


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_quality_gate_registry_immutable_for_owner_and_runtime(quality_gate_registry_case, verb):
    from yta_alpha.models import DataMode

    registry, owner, request = quality_gate_registry_case
    registry.create(owner, DataMode.REPLAY, request)
    statement = {
        "UPDATE": "UPDATE yta.model_quality_gates SET payload=payload",
        "DELETE": "DELETE FROM yta.model_quality_gates",
        "TRUNCATE": "TRUNCATE yta.model_quality_gates CASCADE",
    }[verb]
    for url in (
        os.environ["YTA_TEST_DATABASE_URL"],
        registry.service.price.reports.calibrations.models.url,
    ):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
            connection.rollback()


def test_quality_gate_registry_audit_failure_rolls_back(quality_gate_registry_case, monkeypatch):
    from yta_alpha.models import DataMode

    registry, owner, request = quality_gate_registry_case
    original = registry._connect

    class FailingAudit:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected combined audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield FailingAudit(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected combined audit failure"):
        registry.create(owner, DataMode.REPLAY, request)
    with original() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.model_quality_gates WHERE proposal_id=%s",
                (request.proposal_id,),
            ).fetchone()
            is None
        )


def test_quality_gate_registry_rehashed_tampering_and_columns(quality_gate_registry_case):
    from copy import deepcopy

    from yta_alpha.model_quality_gate_registry import PREFIX as GATE_PREFIX
    from yta_alpha.model_quality_gate_registry import verify_gate
    from yta_alpha.models import DataMode

    registry, owner, request = quality_gate_registry_case
    identity = registry.create(owner, DataMode.REPLAY, request)
    expected = registry.service.evaluate(owner, DataMode.REPLAY, request)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.model_quality_gates WHERE id=%s", (identity,)
        ).fetchone()
    for field, value in [
        ("promotion_allowed", True),
        ("qualification", "VERIFIED"),
        ("numeric_pass", not expected["numeric_pass"]),
        ("price_assessment_id", "historical-quality-assessment-" + "0" * 64),
    ]:
        altered = deepcopy(row)
        altered["payload"][field] = value
        altered["payload"].pop("content_hash")
        digest = content_hash(altered["payload"])
        altered["payload"]["content_hash"] = digest
        altered.update(id=GATE_PREFIX + digest, content_hash=digest)
        with pytest.raises(DomainError, match="reproduced"):
            verify_gate(altered, expected)
    for field, value in [
        ("proposal_id", uuid4()),
        ("data_mode", "DEMO"),
        ("probability_assessment_id", "probability-quality-assessment-" + "0" * 64),
    ]:
        with pytest.raises(DomainError, match="reproduced"):
            verify_gate(row | {field: value}, expected)


def test_quality_gate_http_owner_auth_exact_retry_and_contract(quality_gate_registry_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.model_quality_gate_contracts import ModelQualityGateResponse
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, owner, request = quality_gate_registry_case
    token, tool = (
        "synthetic-combined-service-token-123456789",
        "synthetic-combined-tool-token-123456789",
    )
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.service.price.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-quality-gates"
    headers = {"Authorization": "Bearer " + token, "X-Yta-Owner-Id": str(owner)}
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        for auth in ({}, headers | {"Authorization": "Bearer " + tool}):
            assert client.post(path, json=body, headers=auth).status_code == 401
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer " + token}).status_code
            == 422
        )
        assert (
            client.post(path, json=body, headers=headers | {"X-Yta-Owner-Id": "bad"}).status_code
            == 422
        )
        for extra in (
            {"owner_id": str(owner)},
            {"data_mode": "LIVE_RESEARCH"},
            {"numeric_pass": True},
            {"promotion_allowed": True},
        ):
            assert client.post(path, json=body | extra, headers=headers).status_code == 422
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 200
        saved = created.json()
        identity = "model-quality-gate-" + saved["content_hash"]
        assert saved == registry.get(owner, DataMode.REPLAY, identity)
        assert ModelQualityGateResponse.model_validate(saved).model_dump(mode="json") == saved
        assert client.post(path, json=body, headers=headers).json() == saved
        assert client.get(path + "/" + identity, headers=headers).json() == saved
        assert client.get(path + "/" + identity).status_code == 401
        assert (
            client.get(
                path + "/" + identity, headers=headers | {"Authorization": "Bearer " + tool}
            ).status_code
            == 401
        )
        assert (
            client.get(
                path + "/" + identity, headers={"Authorization": "Bearer " + token}
            ).status_code
            == 422
        )
        for method in ("get", "post"):
            result = (
                client.get(
                    path + "/" + identity, headers=headers | {"X-Yta-Owner-Id": str(uuid4())}
                )
                if method == "get"
                else client.post(
                    path, json=body, headers=headers | {"X-Yta-Owner-Id": str(uuid4())}
                )
            )
            assert result.status_code == 404
        assert (
            client.get(path + "/model-quality-gate-" + "0" * 64, headers=headers).status_code == 404
        )
        assert client.get(path + "/bad", headers=headers).status_code == 422
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_quality_gate_http_default_off_mode_and_unavailable(
    quality_gate_registry_case, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, owner, request = quality_gate_registry_case
    identity = registry.create(owner, DataMode.REPLAY, request)
    token = "synthetic-combined-service-token-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.service.price.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-quality-gates"
    headers = {"Authorization": "Bearer " + token, "X-Yta-Owner-Id": str(owner)}
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.get(path + "/" + identity, headers=headers).status_code == 503
        assert client.post(path, json=body, headers=headers).status_code == 503
    enabled = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(
        create_app(enabled.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}))
    ) as client:
        assert (
            client.get(path + "/" + identity + "?data_mode=REPLAY", headers=headers).status_code
            == 404
        )
        assert (
            client.post(path + "?data_mode=REPLAY", json=body, headers=headers).status_code == 404
        )
    with TestClient(create_app(enabled.model_copy(update={"database_url": None}))) as client:
        assert client.post(path, json=body, headers=headers).status_code == 503

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(enabled)) as client:
        for response in (
            client.get(path + "/" + identity, headers=headers),
            client.post(path, json=body, headers=headers),
        ):
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "TRAINING_UNAVAILABLE"
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.model_quality_gates WHERE proposal_id=%s",
                (request.proposal_id,),
            ).fetchone()["n"]
            == 1
        )


def test_quality_gate_contract_rejects_rehashed_inconsistent_results(quality_gate_registry_case):
    from copy import deepcopy

    from pydantic import ValidationError
    from yta_alpha.model_quality_gate_contracts import ModelQualityGateResponse
    from yta_alpha.models import DataMode

    registry, owner, request = quality_gate_registry_case
    saved = registry.service.evaluate(owner, DataMode.REPLAY, request)
    for changes in (
        {"numeric_pass": not saved["numeric_pass"]},
        {"promotion_allowed": True},
        {"qualification": "VERIFIED"},
        {"promotion_blockers": []},
        {"price_numeric_pass": True, "failed_price_checks": ["minimum_samples"]},
        {"probability_numeric_pass": True, "failed_probability_checks": ["maximum_ece"]},
        {"failed_price_checks": ["minimum_samples", "minimum_samples"]},
        {"failed_probability_checks": ["maximum_ece", "minimum_evaluation_up"]},
    ):
        altered = deepcopy(saved) | changes
        altered.pop("content_hash")
        altered["content_hash"] = content_hash(altered)
        with pytest.raises(ValidationError):
            ModelQualityGateResponse.model_validate(altered)


@pytest.fixture
def combined_review_case(quality_gate_registry_case):
    from yta_alpha.model_review_v2 import CombinedModelReviewService, CombinedReviewRequest
    from yta_alpha.models import DataMode

    gates, owner, gate_request = quality_gate_registry_case
    identity = gates.create(owner, DataMode.REPLAY, gate_request)
    service = CombinedModelReviewService(
        gates.service.price.reports.calibrations.models.url,
        gates.service.price.reports.calibrations.calendar,
    )
    request = CombinedReviewRequest(
        proposal_id=gate_request.proposal_id,
        quality_gate_id=identity,
        action="APPROVE",
        reason="Synthetic combined evidence review",
    )
    return service, owner, request


def test_combined_review_preflight_replays_fixed_gate_without_writes(
    combined_review_case, monkeypatch
):
    from yta_alpha import training
    from yta_alpha.models import DataMode

    service, owner, request = combined_review_case
    reviewer, key = uuid4(), uuid4()
    with service.proposals._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    result = service.evaluate(reviewer, key, DataMode.REPLAY, request)
    gate = service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    assert result == service.evaluate(reviewer, key, DataMode.REPLAY, request)
    assert result["schema_version"] == "model-review-decision-v2"
    assert result["reviewer_id"] == str(reviewer)
    assert result["quality_gate_content_hash"] == gate["content_hash"]
    assert result["price_assessment_id"] == gate["price_assessment_id"]
    assert result["probability_assessment_id"] == gate["probability_assessment_id"]
    assert result["disposition"] == "BLOCKED"
    assert result["promotion_allowed"] is False
    assert "HISTORICAL_EVIDENCE_UNQUALIFIED" in result["blocking_reasons"]
    assert "HUMAN_APPROVAL_REQUIRED" not in result["blocking_reasons"]
    assert result.pop("content_hash") == content_hash(result)
    rejected = service.evaluate(
        reviewer, key, DataMode.REPLAY, request.model_copy(update={"action": "REJECT"})
    )
    assert rejected["disposition"] == "REJECTED"
    assert rejected["blocking_reasons"] == []
    assert rejected["failed_price_checks"] == gate["failed_price_checks"]
    with pytest.raises(DomainError) as error:
        service.evaluate(reviewer, key, DataMode.LIVE_RESEARCH, request)
    assert error.value.status == 404

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Training runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        service.evaluate(reviewer, key, DataMode.REPLAY, request)
    assert error.value.status == 503
    with service.proposals._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_combined_review_rejects_other_proposal_even_with_same_models(combined_review_case):
    from yta_alpha.model_proposals import ProposalRequest
    from yta_alpha.models import DataMode

    service, owner, request = combined_review_case
    proposal = service.proposals.get(owner, DataMode.REPLAY, request.proposal_id)
    p = proposal["proposal"]
    another = service.proposals.create(
        owner,
        uuid4(),
        DataMode.REPLAY,
        ProposalRequest(
            candidate_evaluation_id=p["candidate_evaluation_id"],
            reference_evaluation_id=p["reference_evaluation_id"],
            rationale="Another proposal over identical models",
        ),
    )
    with pytest.raises(DomainError, match="exact proposal"):
        service.evaluate(
            uuid4(),
            uuid4(),
            DataMode.REPLAY,
            request.model_copy(update={"proposal_id": UUID(another["id"])}),
        )


def test_combined_review_retains_separate_price_probability_failures(combined_review_case):
    from yta_alpha.model_quality_gate import ModelQualityGateRequest
    from yta_alpha.models import DataMode

    service, owner, request = combined_review_case
    gate = service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    price_registry, probability_registry = (
        service.gates.service.price,
        service.gates.service.probability,
    )
    price = price_registry.get(gate["price_assessment_id"])
    probability = probability_registry.get(gate["probability_assessment_id"])
    price_rule = price_registry.register_policy(
        price["policy"]
        | {
            "policy_version": price["policy"]["policy_version"] + "-review-strict",
            "minimum_samples": 1000,
        }
    )
    probability_rule = probability_registry.register_policy(
        probability["policy"]
        | {
            "policy_version": probability["policy"]["policy_version"] + "-review-strict",
            "minimum_evaluation_samples": 1000,
        }
    )
    price_id = price_registry.create(
        price_rule, gate["candidate_evaluation_id"], reference_id=gate["reference_evaluation_id"]
    )
    probability_id = probability_registry.create(probability_rule, gate["calibration_id"])
    strict_id = service.gates.create(
        owner,
        DataMode.REPLAY,
        ModelQualityGateRequest(
            proposal_id=request.proposal_id,
            price_assessment_id=price_id,
            probability_assessment_id=probability_id,
        ),
    )
    result = service.evaluate(
        uuid4(), uuid4(), DataMode.REPLAY, request.model_copy(update={"quality_gate_id": strict_id})
    )
    assert result["blocking_reasons"] == [
        "HISTORICAL_EVIDENCE_UNQUALIFIED",
        "PRICE_NUMERIC_GATE_FAILED",
        "PROBABILITY_NUMERIC_GATE_FAILED",
    ]
    assert "minimum_samples" in result["failed_price_checks"]
    assert "minimum_evaluation_samples" in result["failed_probability_checks"]


def test_combined_review_rehashed_references_and_authority_injection(combined_review_case):
    from copy import deepcopy

    from pydantic import ValidationError
    from yta_alpha.model_review_v2 import CombinedReviewRequest, combined_review_decision
    from yta_alpha.models import DataMode

    service, owner, request = combined_review_case
    proposal = service.proposals.get(owner, DataMode.REPLAY, request.proposal_id)
    gate = service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    for field, value in [
        ("proposal_id", str(uuid4())),
        ("proposal_content_hash", "0" * 64),
        ("candidate_model_id", "lightgbm-experimental-" + "0" * 64),
        ("cohort_hash", "0" * 64),
        ("reference_evaluation_id", "model-evaluation-experimental-" + "0" * 64),
    ]:
        altered = deepcopy(gate)
        altered[field] = value
        altered.pop("content_hash")
        altered["content_hash"] = content_hash(altered)
        changed = request.model_copy(
            update={"quality_gate_id": "model-quality-gate-" + altered["content_hash"]}
        )
        with pytest.raises(DomainError, match="exact proposal"):
            combined_review_decision(uuid4(), uuid4(), DataMode.REPLAY, changed, proposal, altered)
    for extra in (
        {"reviewer_id": str(uuid4())},
        {"owner_id": str(owner)},
        {"data_mode": "LIVE_RESEARCH"},
        {"promotion_allowed": True},
        {"assessment_id": gate["price_assessment_id"]},
    ):
        with pytest.raises(ValidationError):
            CombinedReviewRequest.model_validate(request.model_dump(mode="json") | extra)


@pytest.fixture
def combined_review_registry_case(combined_review_case):
    from yta_alpha.model_review_registry import CombinedReviewSubmission, ModelReviewRegistry

    service, owner, request = combined_review_case
    other = uuid4()
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.model_reviews TO yta_app")
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES(%s,%s)", (other, str(other) + "@mixed.test")
        )
    registry = ModelReviewRegistry(
        service.gates.service.price.reports.calibrations.models.url,
        service.gates.service.price.reports.calibrations.calendar,
    )
    return registry, owner, other, CombinedReviewSubmission(**request.model_dump())


def test_combined_review_registry_mixed_chain_exact_old_retry(combined_review_registry_case):
    from yta_alpha.model_review_contracts import ModelReviewPage, ModelReviewResponse
    from yta_alpha.model_review_registry import ReviewSubmission
    from yta_alpha.models import DataMode

    registry, owner, other, request = combined_review_registry_case
    gate = registry.combined_service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    old = ReviewSubmission(
        proposal_id=request.proposal_id,
        assessment_id=gate["price_assessment_id"],
        action="APPROVE",
        reason="Legacy review",
    )
    key = uuid4()
    first = registry.create(owner, key, DataMode.REPLAY, old)
    current = request.model_copy(update={"expected_previous_review_id": UUID(first["id"])})
    second = registry.create_combined(other, uuid4(), DataMode.REPLAY, current)
    assert second["review"]["schema_version"] == "model-review-record-v2"
    assert second["review"]["revision"] == 2
    assert ModelReviewResponse.model_validate(second).model_dump(mode="json") == second
    with pytest.raises(DomainError) as error:
        registry.create_combined(owner, key, DataMode.REPLAY, request)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    closing = old.model_copy(
        update={"action": "REJECT", "expected_previous_review_id": UUID(second["id"])}
    )
    third = registry.create(owner, uuid4(), DataMode.REPLAY, closing)
    assert third["review"]["revision"] == 3
    assert registry.create(owner, key, DataMode.REPLAY, old) == first
    page = registry.history(request.proposal_id, DataMode.REPLAY, limit=2)
    assert page["items"] == [third, second]
    assert ModelReviewPage.model_validate(page).model_dump(mode="json") == page
    assert registry.history(request.proposal_id, DataMode.REPLAY, cursor=second["id"])["items"] == [
        first
    ]
    with pytest.raises(DomainError) as error:
        registry.create_combined(
            owner,
            uuid4(),
            DataMode.REPLAY,
            request.model_copy(update={"expected_previous_review_id": UUID(third["id"])}),
        )
    assert error.value.code == "MODEL_REVIEW_CLOSED"


def test_combined_review_registry_concurrency_and_cross_version_race(combined_review_registry_case):
    from yta_alpha.model_review_registry import ReviewSubmission
    from yta_alpha.models import DataMode

    registry, owner, other, request = combined_review_registry_case
    key = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: registry.create_combined(owner, key, DataMode.REPLAY, request), range(4)
            )
        )
    assert results == [results[0]] * 4
    first = results[0]
    gate = registry.combined_service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    old = ReviewSubmission(
        proposal_id=request.proposal_id,
        assessment_id=gate["price_assessment_id"],
        action="APPROVE",
        reason="Legacy race",
        expected_previous_review_id=UUID(first["id"]),
    )
    new = request.model_copy(update={"expected_previous_review_id": UUID(first["id"])})

    def submit(kind):
        try:
            return (registry.create if kind == 0 else registry.create_combined)(
                other, uuid4(), DataMode.REPLAY, old if kind == 0 else new
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(submit, range(2)))
    assert sum(isinstance(row, dict) for row in raced) == 1
    assert "MODEL_REVIEW_STALE" in raced
    with registry._connect() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM yta.model_reviews WHERE proposal_id=%s",
                (request.proposal_id,),
            ).fetchone()["n"]
            == 2
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (first["id"],)
            ).fetchone()["n"]
            == 1
        )


def test_combined_review_registry_audit_rollback(combined_review_registry_case, monkeypatch):
    from yta_alpha.models import DataMode

    registry, owner, _, request = combined_review_registry_case
    original = registry._connect

    class FailAudit:
        def __init__(self, c):
            self.c = c

        def execute(self, q, params=None):
            if "INSERT INTO yta.audit_events" in q:
                raise RuntimeError("mixed audit failure")
            return self.c.execute(q, params)

    @contextmanager
    def broken():
        with original() as c:
            yield FailAudit(c)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="mixed audit failure"):
        registry.create_combined(owner, uuid4(), DataMode.REPLAY, request)
    with original() as c:
        assert (
            c.execute(
                "SELECT id FROM yta.model_reviews WHERE proposal_id=%s", (request.proposal_id,)
            ).fetchone()
            is None
        )


def test_combined_review_registry_tampering_and_v2_rejection(combined_review_registry_case):
    from copy import deepcopy

    from yta_alpha.model_review_registry import verify_review
    from yta_alpha.models import DataMode

    registry, owner, _, request = combined_review_registry_case
    first = registry.create_combined(owner, uuid4(), DataMode.REPLAY, request)
    proposal = registry.service.proposals.get(owner, DataMode.REPLAY, request.proposal_id)
    gate = registry.combined_service.gates.get(owner, DataMode.REPLAY, request.quality_gate_id)
    price = registry.service.quality.get(gate["price_assessment_id"])
    with registry._connect() as c:
        row = c.execute("SELECT * FROM yta.model_reviews WHERE id=%s", (first["id"],)).fetchone()
    for field, value in [
        ("quality_gate_id", None),
        ("assessment_id", "historical-quality-assessment-" + "0" * 64),
    ]:
        with pytest.raises(DomainError):
            verify_review(row | {field: value}, proposal, price, None, gate)
    altered = deepcopy(row)
    altered["payload"]["promotion_allowed"] = True
    altered["payload"].pop("content_hash")
    digest = content_hash(altered["payload"])
    altered["payload"]["content_hash"] = digest
    altered["content_hash"] = digest
    with pytest.raises(DomainError):
        verify_review(altered, proposal, price, None, gate)
    closing = request.model_copy(
        update={"action": "REJECT", "expected_previous_review_id": UUID(first["id"])}
    )
    second = registry.create_combined(owner, uuid4(), DataMode.REPLAY, closing)
    assert second["review"]["disposition"] == "REJECTED"
    assert second["review"]["blocking_reasons"] == []
    assert registry.get(first["id"], DataMode.REPLAY) == first


def test_reviewer_quality_gate_cross_owner_audit_and_http(quality_gate_registry_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, owner, request = quality_gate_registry_case
    reviewer = uuid4()
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
            (reviewer, str(reviewer) + "@gate-review.test"),
        )
    with pytest.raises(DomainError) as error:
        registry.create(reviewer, DataMode.REPLAY, request)
    assert error.value.status == 404
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(
            pool.map(
                lambda _: registry.create_for_review(reviewer, DataMode.REPLAY, request), range(4)
            )
        )
    assert len(set(identities)) == 1
    identity = identities[0]
    expected = registry.get(owner, DataMode.REPLAY, identity)
    assert registry.get_for_review(DataMode.REPLAY, identity) == expected
    assert registry.create(owner, DataMode.REPLAY, request) == identity
    with registry._connect() as connection:
        audits = connection.execute(
            "SELECT actor_id FROM yta.audit_events WHERE resource_id=%s", (identity,)
        ).fetchall()
    assert audits == [{"actor_id": reviewer}]
    token, tool = "synthetic-review-gate-service-123456", "synthetic-review-gate-tool-12345678"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.service.price.reports.calibrations.models.url),
    )
    path = "/internal/v1/model-review-quality-gates"
    headers = {"Authorization": "Bearer " + token, "X-Yta-Owner-Id": str(reviewer)}
    body = request.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        for h in ({}, headers | {"Authorization": "Bearer " + tool}):
            assert client.post(path, json=body, headers=h).status_code == 401
            assert client.get(path + "/" + identity, headers=h).status_code == 401
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer " + token}).status_code
            == 422
        )
        for extra in (
            {"owner_id": str(owner)},
            {"reviewer_id": str(reviewer)},
            {"promotion_allowed": True},
        ):
            assert client.post(path, json=body | extra, headers=headers).status_code == 422
        assert client.post(path, json=body, headers=headers).json() == expected
        assert client.get(path + "/" + identity, headers=headers).json() == expected
        assert (
            client.get("/internal/v1/model-quality-gates/" + identity, headers=headers).status_code
            == 404
        )
    for update, status in (
        ({"experimental_models": "DISABLED"}, 503),
        ({"data_mode": DataMode.LIVE_RESEARCH}, 404),
    ):
        with TestClient(create_app(settings.model_copy(update=update))) as client:
            assert client.post(path, json=body, headers=headers).status_code == status
            assert client.get(path + "/" + identity, headers=headers).status_code == status


@pytest.fixture
def backtest_signal_case(setup):
    from yta_alpha.backtest_signals import BacktestSignalService, RegisteredBacktestRequest

    registry, identity, source, _ = setup
    service = BacktestSignalService(registry.models.url, registry.calendar)
    requests = tuple(
        RegisteredBacktestRequest(
            signal_id=uuid4(), model_id=identity, snapshot=snapshot, decision_at=snapshot.as_of
        )
        for snapshot in source.snapshots[:2]
    )
    return service, requests, source.snapshots[-1]


def test_registered_backtest_replays_inference_and_fixed_signal_provenance(backtest_signal_case):
    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.experimental_predictions import ExperimentalRequest, experimental_predict
    from yta_alpha.models import DataMode

    service, requests, observation = backtest_signal_case
    with service.models._connect() as connection:
        before = connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
    signal, source = service.evaluate(requests[0], mode=DataMode.REPLAY, cutoff=observation.as_of)
    expected = experimental_predict(
        service.models,
        requests[0].model_id,
        ExperimentalRequest(snapshot=requests[0].snapshot, horizon_sessions=5),
        calendar=service.calendar,
        now=requests[0].decision_at,
        replay=True,
    )
    assert source["prediction"] == expected.model_dump(mode="json")
    assert signal.action == ("LONG" if expected.expected_return > 0 else "CASH")
    assert source["content_hash"] == signal.source_signal_hash
    assert (
        content_hash({k: v for k, v in source.items() if k != "content_hash"})
        == source["content_hash"]
    )
    assert source["qualification"] == "UNVERIFIED"
    assert source["registered_at"] and source["training_evidence_verification"] == "not-retrained"
    costs = ExecutionCosts(commission_rate="0.001", slippage_rate="0.001")
    kwargs = dict(mode=DataMode.REPLAY, cutoff=observation.as_of, costs=costs)
    report = service.report(requests, observation, **kwargs)
    assert service.report(tuple(reversed(requests)), observation, **kwargs) == report
    assert report["signal_verification"] == "registered-native-inference-replayed"
    assert report["schema_version"] == "registered-next-open-backtest-report-v2"
    assert report["computed_count"] == report["overlap_count"] == 1
    assert len(report["signal_sources"]) == 2
    assert report["signal_sources"][0] == source
    assert report["rows"][0]["episode"]["holding_sessions"] == 5
    assert (
        content_hash({k: v for k, v in report.items() if k != "content_hash"})
        == report["content_hash"]
    )
    with service.models._connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM yta.audit_events").fetchone()["n"]
            == before
        )


def test_registered_backtest_forbids_action_injection_wrong_mode_and_late_decision(
    backtest_signal_case,
):
    from pydantic import ValidationError
    from yta_alpha.backtest_signals import RegisteredBacktestRequest
    from yta_alpha.models import DataMode

    service, requests, observation = backtest_signal_case
    request = requests[0]
    for extra in (
        {"action": "LONG"},
        {"source_signal_hash": "a" * 64},
        {"holding_sessions": True},
        {"promotion_allowed": True},
    ):
        with pytest.raises(ValidationError):
            RegisteredBacktestRequest.model_validate(request.model_dump(mode="python") | extra)
    for mode in (DataMode.LIVE_RESEARCH, DataMode.DEMO):
        with pytest.raises(DomainError) as error:
            service.evaluate(request, mode=mode, cutoff=observation.as_of)
        assert error.value.code == "BACKTEST_COHORT_MISMATCH"
    late = service.calendar.open_at(service.calendar.after(request.snapshot.bars[-1].session, 1))
    with pytest.raises(DomainError) as error:
        service.evaluate(
            request.model_copy(update={"decision_at": late}),
            mode=DataMode.REPLAY,
            cutoff=observation.as_of,
        )
    assert error.value.code == "BACKTEST_DECISION_WINDOW"
    with pytest.raises(DomainError) as error:
        service.evaluate(
            request.model_copy(update={"model_id": "lightgbm-experimental-" + "0" * 64}),
            mode=DataMode.REPLAY,
            cutoff=observation.as_of,
        )
    assert error.value.code == "MODEL_NOT_FOUND"


def test_registered_backtest_never_falls_back_when_native_runtime_unavailable(
    backtest_signal_case, monkeypatch
):
    from yta_alpha import training
    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.models import DataMode

    service, requests, observation = backtest_signal_case

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        service.report(
            requests,
            observation,
            mode=DataMode.REPLAY,
            cutoff=observation.as_of,
            costs=ExecutionCosts(commission_rate="0", slippage_rate="0"),
        )
    assert error.value.code == "TRAINING_UNAVAILABLE" and error.value.status == 503


def test_backtest_observation_never_changes_the_registered_input_signal(backtest_signal_case):
    from decimal import Decimal

    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.models import DataMode

    service, requests, observation = backtest_signal_case
    bars = list(observation.bars)
    entry = service.calendar.after(requests[0].snapshot.bars[-1].session, 1)
    for i, bar in enumerate(bars):
        if bar.session == entry:
            bars[i] = bar.model_copy(
                update={field: Decimal(200) for field in ("open", "high", "low", "close")}
            )
    changed = observation.model_copy(update={"bars": tuple(bars)})
    kwargs = dict(
        mode=DataMode.REPLAY,
        cutoff=observation.as_of,
        costs=ExecutionCosts(commission_rate="0", slippage_rate="0"),
    )
    original = service.report(requests, observation, **kwargs)
    revised = service.report(requests, changed, **kwargs)
    assert original["signal_sources"] == revised["signal_sources"]
    assert original["content_hash"] != revised["content_hash"]
    assert (
        original["rows"][0]["episode"]["benchmark_net_return"]
        != revised["rows"][0]["episode"]["benchmark_net_return"]
    )


def test_registered_backtest_rejects_model_trained_after_input_window(backtest_signal_case):
    from datetime import timedelta

    from yta_alpha.models import DataMode

    service, requests, observation = backtest_signal_case
    request = requests[0]
    artifact = service.models.get(request.model_id).artifact
    day = service.calendar.after(service.calendar.latest_closed(artifact.training_cutoff), -2)
    at = service.calendar.close_at(day) + timedelta(minutes=6)
    bars = tuple(
        bar.model_copy(
            update={
                "available_at": service.calendar.close_at(bar.session) + timedelta(minutes=1),
                "retrieved_at": service.calendar.close_at(bar.session) + timedelta(minutes=2),
            }
        )
        for bar in request.snapshot.bars
        if bar.session <= day
    )
    assert bars and bars[-1].session == day and at < artifact.training_cutoff
    early = request.snapshot.model_copy(
        update={"bars": bars, "as_of": at, "splits": (), "actions_verified_through": day}
    )
    with pytest.raises(DomainError) as error:
        service.evaluate(
            request.model_copy(update={"snapshot": early, "decision_at": at}),
            mode=DataMode.REPLAY,
            cutoff=observation.as_of,
        )
    assert error.value.code == "FUTURE_INFORMATION"


@pytest.fixture
def backtest_registry_case(backtest_signal_case):
    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.backtest_registry import BacktestInput, BacktestRegistry

    service, requests, observation = backtest_signal_case
    with psycopg.connect(os.environ["YTA_TEST_DATABASE_URL"]) as connection:
        connection.execute(
            "GRANT SELECT,INSERT ON yta.backtest_reports,yta.backtest_report_models TO yta_app"
        )
    registry = BacktestRegistry(service.models.url, service.calendar)
    value = BacktestInput(
        requests=requests,
        observation=observation,
        cutoff=observation.as_of,
        costs=ExecutionCosts(commission_rate="0.001", slippage_rate="0.002"),
    )
    return registry, value


def test_backtest_registry_concurrent_registration_replay_and_exact_retry(backtest_registry_case):
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: registry.create(DataMode.REPLAY, value), range(4)))
    assert len(set(identities)) == 1
    identity = identities[0]
    assert (
        registry.create(
            DataMode.REPLAY, value.model_copy(update={"requests": tuple(reversed(value.requests))})
        )
        == identity
    )
    saved = registry.get(identity, mode=DataMode.REPLAY)
    assert saved["report"] == registry.replay(DataMode.REPLAY, value)
    assert saved["input"]["costs"] == value.costs.model_dump(mode="json")
    assert saved["code_sha256"]
    with registry._connect() as connection:
        reports = connection.execute(
            "SELECT count(*) AS n FROM yta.backtest_reports WHERE id=%s", (identity,)
        ).fetchone()["n"]
        models = connection.execute(
            "SELECT model_id FROM yta.backtest_report_models WHERE report_id=%s", (identity,)
        ).fetchall()
        audits = connection.execute(
            "SELECT actor_id,action FROM yta.audit_events WHERE resource_id=%s", (identity,)
        ).fetchall()
    assert reports == 1
    assert models == [{"model_id": value.requests[0].model_id}]
    assert audits == [{"actor_id": None, "action": "model.backtest_registered"}]


@pytest.mark.parametrize("table", ["backtest_reports", "backtest_report_models"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_backtest_facts_and_model_links_are_immutable(backtest_registry_case, table, verb):
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    registry.create(DataMode.REPLAY, value)
    column = "id" if table == "backtest_reports" else "report_id"
    statement = {
        "UPDATE": f"UPDATE yta.{table} SET {column}={column}",
        "DELETE": f"DELETE FROM yta.{table}",
        "TRUNCATE": f"TRUNCATE yta.{table} CASCADE",
    }[verb]
    for url in (os.environ["YTA_TEST_DATABASE_URL"], registry.service.models.url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
            connection.rollback()


def test_backtest_audit_failure_rolls_back_report_and_references(
    backtest_registry_case, monkeypatch
):
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    original = registry._connect
    with original() as connection:
        before = tuple(
            connection.execute(f"SELECT count(*) AS n FROM yta.{table}").fetchone()["n"]
            for table in ("backtest_reports", "backtest_report_models")
        )

    class Failing:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO yta.audit_events" in query:
                raise RuntimeError("injected backtest audit failure")
            return self.connection.execute(query, params)

    @contextmanager
    def broken():
        with original() as connection:
            yield Failing(connection)

    monkeypatch.setattr(registry, "_connect", broken)
    with pytest.raises(RuntimeError, match="injected backtest audit failure"):
        registry.create(DataMode.REPLAY, value)
    with original() as connection:
        after = tuple(
            connection.execute(f"SELECT count(*) AS n FROM yta.{table}").fetchone()["n"]
            for table in ("backtest_reports", "backtest_report_models")
        )
    assert after == before


def test_backtest_semantic_replay_rejects_rehashed_metrics_and_missing_model_links(
    backtest_registry_case,
):
    from copy import deepcopy

    from yta_alpha.backtest_registry import PREFIX, verify_backtest
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    identity = registry.create(DataMode.REPLAY, value)
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.backtest_reports WHERE id=%s", (identity,)
        ).fetchone()
    replayed = registry.replay(DataMode.REPLAY, value)
    with pytest.raises(DomainError):
        verify_backtest(row, replayed, [])
    tampered = deepcopy(row)
    tampered["payload"]["report"]["strategy_net_return"] = "100"
    report = tampered["payload"]["report"]
    report["content_hash"] = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    tampered["content_hash"] = content_hash(tampered["payload"])
    tampered["id"] = PREFIX + tampered["content_hash"]
    with pytest.raises(DomainError) as error:
        verify_backtest(tampered, replayed, [value.requests[0].model_id])
    assert error.value.code == "BACKTEST_INTEGRITY_ERROR"
    with pytest.raises(DomainError):
        verify_backtest(row | {"data_mode": "DEMO"}, replayed, [value.requests[0].model_id])


def test_backtest_registry_mode_scope_and_native_failure(backtest_registry_case, monkeypatch):
    from yta_alpha import training
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    identity = registry.create(DataMode.REPLAY, value)
    with pytest.raises(DomainError) as error:
        registry.get(identity, mode=DataMode.DEMO)
    assert error.value.status == 404
    with pytest.raises(DomainError):
        registry.create(DataMode.LIVE_RESEARCH, value)

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native replay unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        registry.get(identity, mode=DataMode.REPLAY)
    assert error.value.status == 503


def test_backtest_registry_preserves_incomplete_report_and_versions_cost_changes(
    backtest_registry_case,
):
    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    entry = registry.service.calendar.after(value.requests[0].snapshot.bars[-1].session, 1)
    observation = value.observation.model_copy(
        update={"bars": tuple(b for b in value.observation.bars if b.session != entry)}
    )
    incomplete = value.model_copy(update={"observation": observation})
    first = registry.create(DataMode.REPLAY, incomplete)
    saved = registry.get(first, mode=DataMode.REPLAY)
    assert saved["report"]["status"] == "INCOMPLETE"
    assert saved["report"]["strategy_net_return"] is None
    assert saved["report"]["evidence_unavailable_count"] == 1
    second = registry.create(
        DataMode.REPLAY,
        value.model_copy(update={"costs": ExecutionCosts(commission_rate="0", slippage_rate="0")}),
    )
    assert first != second
    assert registry.get(first, mode=DataMode.REPLAY) == saved


def test_backtest_http_service_auth_exact_retry_and_private_projection(backtest_registry_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.backtest_contracts import BacktestResponse
    from yta_alpha.settings import Settings

    registry, value = backtest_registry_case
    token, tool = "synthetic-backtest-service-12345678", "synthetic-backtest-tools-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.service.models.url),
    )
    path = "/internal/v1/backtest-reports"
    headers = {"Authorization": "Bearer " + token}
    body = value.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer " + tool}).status_code
            == 401
        )
        for extra in ({"data_mode": "LIVE_RESEARCH"}, {"promotion_allowed": True}, {"report": {}}):
            assert client.post(path, json=body | extra, headers=headers).status_code == 422
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 200
        saved = created.json()
        assert BacktestResponse.model_validate(saved).model_dump(mode="json") == saved
        assert set(saved) == {"id", "content_hash", "input_hash", "code_sha256", "report"}
        assert saved["input_hash"] == content_hash(
            body | {"requests": sorted(body["requests"], key=lambda r: r["signal_id"])}
        )
        assert client.post(path, json=body, headers=headers).json() == saved
        assert client.get(path + "/" + saved["id"], headers=headers).json() == saved
        assert client.get(path + "/" + saved["id"]).status_code == 401
        assert client.get(path + "/bad", headers=headers).status_code == 422
        assert client.get(path + "/backtest-report-" + "0" * 64, headers=headers).status_code == 404
        assert "observation" not in saved and "input" not in saved
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s", (saved["id"],)
            ).fetchone()["n"]
            == 1
        )


def test_backtest_http_default_off_mode_storage_and_native_unavailable(
    backtest_registry_case, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha import training
    from yta_alpha.api import create_app
    from yta_alpha.models import DataMode
    from yta_alpha.settings import Settings

    registry, value = backtest_registry_case
    identity = registry.create(DataMode.REPLAY, value)
    token = "synthetic-backtest-service-12345678"
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(registry.service.models.url),
    )
    path = "/internal/v1/backtest-reports"
    headers = {"Authorization": "Bearer " + token}
    body = value.model_dump(mode="json")
    for update in ({}, {"experimental_models": "ENABLED", "database_url": None}):
        with TestClient(create_app(settings.model_copy(update=update))) as client:
            assert client.post(path, json=body, headers=headers).status_code == 503
            assert client.get(path + "/" + identity, headers=headers).status_code == 503
    enabled = settings.model_copy(update={"experimental_models": "ENABLED"})
    with TestClient(create_app(enabled.model_copy(update={"data_mode": DataMode.DEMO}))) as client:
        assert client.get(path + "/" + identity, headers=headers).status_code == 404
        assert (
            client.post(path, json=body, headers=headers).json()["error"]["code"]
            == "BACKTEST_COHORT_MISMATCH"
        )

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native replay unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with TestClient(create_app(enabled)) as client:
        assert client.post(path, json=body, headers=headers).status_code == 503
        assert client.get(path + "/" + identity, headers=headers).status_code == 503


def test_backtest_contract_rejects_rehashed_equity_cost_source_and_count_tampering(
    backtest_registry_case,
):
    from copy import deepcopy

    from pydantic import ValidationError
    from yta_alpha.backtest_contracts import BacktestResponse
    from yta_alpha.backtest_reports import backtest_response
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    identity = registry.create(DataMode.REPLAY, value)
    saved = backtest_response(registry, identity, DataMode.REPLAY).model_dump(mode="json")
    mutations = [
        lambda r: r.update(computed_count=True),
        lambda r: r.update(strategy_net_return="10"),
        lambda r: r["realized_equity_curve"][0].update(equity="10"),
        lambda r: r["costs"].update(commission_rate="0.99"),
        lambda r: r["rows"][1].update(overlaps_signal_id=str(uuid4())),
        lambda r: r["rows"][0].update(source_signal_hash="0" * 64),
        lambda r: r["signal_sources"].reverse(),
        lambda r: r.update(data_mode="LIVE_RESEARCH"),
    ]
    for mutate in mutations:
        changed = deepcopy(saved)
        report = changed["report"]
        mutate(report)
        report["content_hash"] = content_hash(
            {k: v for k, v in report.items() if k != "content_hash"}
        )
        with pytest.raises(ValidationError):
            BacktestResponse.model_validate(changed)
    entry = registry.service.calendar.after(value.requests[0].snapshot.bars[-1].session, 1)
    missing = value.observation.model_copy(
        update={"bars": tuple(b for b in value.observation.bars if b.session != entry)}
    )
    incomplete_id = registry.create(
        DataMode.REPLAY, value.model_copy(update={"observation": missing})
    )
    incomplete = backtest_response(registry, incomplete_id, DataMode.REPLAY).model_dump(mode="json")
    assert incomplete["report"]["strategy_net_return"] is None
    changed = deepcopy(incomplete)
    changed["report"]["strategy_net_return"] = "0"
    changed["report"]["content_hash"] = content_hash(
        {k: v for k, v in changed["report"].items() if k != "content_hash"}
    )
    with pytest.raises(ValidationError):
        BacktestResponse.model_validate(changed)


@pytest.fixture
def backtest_catalog_case(backtest_registry_case):
    from yta_alpha.backtest import ExecutionCosts
    from yta_alpha.models import DataMode

    registry, value = backtest_registry_case
    first = registry.create(DataMode.REPLAY, value)
    second = registry.create(
        DataMode.REPLAY,
        value.model_copy(
            update={"costs": ExecutionCosts(commission_rate="0.003", slippage_rate="0.004")}
        ),
    )
    entry = registry.service.calendar.after(value.requests[0].snapshot.bars[-1].session, 1)
    incomplete = registry.create(
        DataMode.REPLAY,
        value.model_copy(
            update={
                "observation": value.observation.model_copy(
                    update={"bars": tuple(b for b in value.observation.bars if b.session != entry)}
                )
            }
        ),
    )
    return registry, value, (first, second, incomplete)


def test_backtest_catalog_keyset_scope_native_independence_and_readonly(
    backtest_catalog_case, monkeypatch
):
    from yta_alpha.backtest_catalog import backtest_page
    from yta_alpha.models import DataMode

    registry, value, identities = backtest_catalog_case
    model = value.requests[0].model_id

    def unexpected(*args, **kwargs):
        raise AssertionError("Catalog must not claim native replay")

    monkeypatch.setattr(registry, "replay", unexpected)
    with registry._connect() as connection:
        before = connection.execute(
            "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=ANY(%s)",
            (list(identities),),
        ).fetchone()["n"]
    page = backtest_page(registry, DataMode.REPLAY, model_id=model, limit=1)
    seen = []
    while True:
        assert len(page.items) <= 1
        for row in page.items:
            assert row.verification == "stored-integrity-checked-native-replay-on-detail"
            assert model in row.model_ids
            assert (
                row.signal_count
                == row.computed_count + row.overlap_count + row.evidence_unavailable_count
            )
            seen.append(row.id)
        if page.next_cursor is None:
            break
        assert page.next_cursor == page.items[-1].id
        page = backtest_page(
            registry, DataMode.REPLAY, model_id=model, cursor=page.next_cursor, limit=1
        )
    assert len(seen) == len(set(seen))
    assert set(identities) <= set(seen)
    filtered = backtest_page(registry, DataMode.REPLAY, model_id=model, status="INCOMPLETE")
    assert identities[2] in {r.id for r in filtered.items}
    assert all(r.status == "INCOMPLETE" for r in filtered.items)
    assert not backtest_page(registry, DataMode.DEMO, model_id=model).items
    for kwargs in (
        {"status": "COMPLETE", "cursor": identities[2]},
        {"model_id": "lightgbm-experimental-" + "0" * 64, "cursor": identities[0]},
    ):
        with pytest.raises(DomainError, match="outside"):
            backtest_page(registry, DataMode.REPLAY, **kwargs)
    for kwargs in (
        {"limit": True},
        {"limit": 51},
        {"status": "EMPTY"},
        {"cursor": "bad"},
        {"model_id": "bad"},
    ):
        with pytest.raises(DomainError):
            backtest_page(registry, DataMode.REPLAY, **kwargs)
    with pytest.raises(DomainError):
        backtest_page(registry, DataMode.LIVE_RESEARCH)
    with registry._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=ANY(%s)",
                (list(identities),),
            ).fetchone()["n"]
            == before
        )


def test_backtest_catalog_rejects_rehashed_metrics_references_and_input(backtest_catalog_case):
    from copy import deepcopy

    from yta_alpha.backtest_catalog import summary

    registry, value, identities = backtest_catalog_case
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT * FROM yta.backtest_reports WHERE id=%s", (identities[0],)
        ).fetchone()
    references = sorted({r.model_id for r in value.requests})
    for change in ("metrics", "input", "references"):
        tampered = deepcopy(row)
        if change == "metrics":
            report = tampered["payload"]["report"]
            report["strategy_net_return"] = "10"
            report["content_hash"] = content_hash(
                {k: v for k, v in report.items() if k != "content_hash"}
            )
        if change == "input":
            tampered["payload"]["input"]["unexpected"] = True
        tampered["content_hash"] = content_hash(tampered["payload"])
        tampered["id"] = "backtest-report-" + tampered["content_hash"]
        with pytest.raises(DomainError) as error:
            summary(tampered, [] if change == "references" else references)
        assert error.value.code == "BACKTEST_INTEGRITY_ERROR"


def test_backtest_catalog_http_auth_filters_and_default_off(backtest_catalog_case):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from yta_alpha.api import create_app
    from yta_alpha.backtest_catalog import BacktestPage
    from yta_alpha.settings import Settings

    registry, value, identities = backtest_catalog_case
    token, tool = "synthetic-catalog-service-12345678", "synthetic-catalog-tools-123456789"
    settings = Settings(
        _env_file=None,
        env="test",
        experimental_models="ENABLED",
        alpha_token=SecretStr(token),
        alpha_tools_token=SecretStr(tool),
        database_url=SecretStr(registry.service.models.url),
    )
    path = "/internal/v1/backtest-reports"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + tool}).status_code == 401
        response = client.get(
            path,
            params={"model_id": value.requests[0].model_id, "status": "INCOMPLETE"},
            headers=headers,
        )
        assert response.status_code == 200
        page = BacktestPage.model_validate(response.json())
        assert identities[2] in {r.id for r in page.items}
        for params in ({"status": "EMPTY"}, {"limit": 51}):
            assert client.get(path, params=params, headers=headers).status_code == 422
        assert (
            client.get(
                path, params={"cursor": "backtest-report-" + "0" * 64}, headers=headers
            ).json()["error"]["code"]
            == "INVALID_CURSOR"
        )
    with TestClient(
        create_app(settings.model_copy(update={"experimental_models": "DISABLED"}))
    ) as client:
        assert client.get(path, headers=headers).status_code == 503
