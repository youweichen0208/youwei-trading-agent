import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from yta_alpha.api import create_app
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import replay_observation, replay_snapshot
from yta_alpha.migrate import migrate
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db():
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set YTA_TEST_DATABASE_URL for real PostgreSQL integration tests")
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test"), "Tests require a dedicated *_test database"
    migrate(url, ROOT / "infra/migrations")
    return url


@pytest.fixture(scope="module")
def calendar():
    return TradingCalendar()


@pytest.fixture
def store(db, calendar):
    runtime = os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL")
    if not runtime:
        config = conninfo_to_dict(db)
        config.update(user="yta_app", password="local-yta-app")
        runtime = make_conninfo(**config)
    return ResearchStore(runtime, calendar, "a" * 40)


@pytest.fixture
def owner(db):
    user = uuid4()
    with psycopg.connect(db) as connection:
        connection.execute(
            "INSERT INTO yta.users(id, email) VALUES (%s, %s)", (user, f"{user}@test.invalid")
        )
    return user


@pytest.fixture
def isolated_evidence_store(db, store, calendar):
    """Corrupt fact fixtures must never poison the shared backup/restore dataset."""
    from psycopg import sql

    name = "yta_corrupt_evidence_" + uuid4().hex + "_test"
    owner_config = conninfo_to_dict(db)
    runtime_config = conninfo_to_dict(store.url)
    with psycopg.connect(db, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            owner_config["dbname"] = name
            runtime_config["dbname"] = name
            owner_url = make_conninfo(**owner_config)
            migrate(owner_url, ROOT / "infra/migrations")
            identity = uuid4()
            with psycopg.connect(owner_url) as connection:
                role = sql.Identifier(runtime_config["user"])
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA yta TO {}").format(role))
                connection.execute(
                    sql.SQL("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO {}").format(role)
                )
                connection.execute(
                    sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO {}").format(role)
                )
                connection.execute(
                    "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
                    (identity, f"{identity}@corrupt.test"),
                )
            yield ResearchStore(make_conninfo(**runtime_config), calendar, "a" * 40), identity
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def draft(store, owner, calendar):
    snapshot = replay_snapshot(calendar)
    return store.create_draft(owner, snapshot, snapshot.as_of), snapshot


def test_freeze_is_atomic_immutable_and_replayable(db, store, owner, draft):
    value, snapshot = draft
    draft_id = UUID(value["draft_id"])
    frozen = store.freeze(owner, draft_id, "confirm-once", confirmed=True, now=snapshot.as_of)
    repeated = store.freeze(
        owner, draft_id, "confirm-once", confirmed=True, now=snapshot.as_of + timedelta(days=100)
    )
    assert frozen == repeated
    assert frozen["status"] == "FROZEN"
    assert len(frozen["events"]) == 1
    for statement in [
        "UPDATE yta.predictions SET model_version = model_version WHERE id = %s",
        "DELETE FROM yta.predictions WHERE id = %s",
    ]:
        with (
            psycopg.connect(db) as connection,
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        ):
            connection.execute(statement, (frozen["prediction_id"],))
    # A statement-level guard also blocks table truncation, even under migration-owner rights.
    with (
        psycopg.connect(db) as connection,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        connection.execute("TRUNCATE yta.audit_events")
    with store._connect() as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("UPDATE yta.predictions SET model_version = model_version")


def test_concurrent_duplicate_freezes_create_one_fact(db, store, owner, draft):
    value, snapshot = draft
    draft_id = UUID(value["draft_id"])

    def freeze_one(index):
        return store.freeze(
            owner, draft_id, f"concurrent-{index % 3}", confirmed=True, now=snapshot.as_of
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(freeze_one, range(12)))
    assert len({r["prediction_id"] for r in results}) == 1
    with psycopg.connect(db) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.predictions WHERE owner_id = %s", (owner,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events "
                "WHERE actor_id = %s AND action = 'prediction.freeze'",
                (owner,),
            ).fetchone()[0]
            == 1
        )


def test_same_key_different_draft_conflicts_without_writing(store, owner, draft):
    value, snapshot = draft
    first = store.freeze(
        owner, UUID(value["draft_id"]), "same-key", confirmed=True, now=snapshot.as_of
    )
    other = store.create_draft(owner, snapshot, snapshot.as_of)
    with pytest.raises(DomainError) as error:
        store.freeze(owner, UUID(other["draft_id"]), "same-key", confirmed=True, now=snapshot.as_of)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert [p["prediction_id"] for p in store.list_predictions(owner)] == [first["prediction_id"]]


def test_no_confirmation_wrong_owner_and_expiry_do_not_write(store, owner, draft):
    value, snapshot = draft
    args = (owner, UUID(value["draft_id"]), "invalid")
    with pytest.raises(DomainError) as error:
        store.freeze(*args, confirmed=False, now=snapshot.as_of)
    assert error.value.code == "CONFIRMATION_REQUIRED"
    with pytest.raises(DomainError) as error:
        store.freeze(uuid4(), args[1], args[2], confirmed=True, now=snapshot.as_of)
    assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        store.freeze(*args, confirmed=True, now=snapshot.as_of + timedelta(days=5))
    assert error.value.code == "DRAFT_EXPIRED"
    assert store.list_predictions(owner) == []


def test_settlement_retries_and_revisions_preserve_every_version(store, owner, draft, calendar):
    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "freeze", confirmed=True, now=snapshot.as_of
    )
    prediction_id = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    with pytest.raises(DomainError) as error:
        store.settle_prediction(owner, prediction_id, observed, now=snapshot.as_of)
    assert error.value.code == "NOT_DUE"
    assert not store.get_prediction(owner, prediction_id)["outcomes"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: store.settle_prediction(
                    owner, prediction_id, observed, now=observed.as_of
                ),
                range(4),
            )
        )
    assert all(len(r["outcomes"]) == 1 for r in results)
    assert results[0]["outcomes"][0]["result"]["actual_return"] == "0.020000000000"
    revised = replay_observation(calendar, "103")
    final = store.settle_prediction(owner, prediction_id, revised, now=revised.as_of)
    assert [o["settlement_version"] for o in final["outcomes"]] == [1, 2]
    assert final["outcomes"][0] == results[0]["outcomes"][0]
    assert final["forecast"] == frozen["forecast"]
    assert final["outcomes"][1]["result"]["actual_return"] == "0.030000000000"


def test_cross_owner_read_is_not_found(store, owner, draft):
    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "freeze", confirmed=True, now=snapshot.as_of
    )
    with pytest.raises(DomainError) as error:
        store.get_prediction(uuid4(), UUID(frozen["prediction_id"]))
    assert error.value.status == 404


def test_migrations_are_repeatable_and_checksums_enforced(db, tmp_path):
    assert migrate(db, ROOT / "infra/migrations") == []
    source = ROOT / "infra/migrations/001_research_facts.sql"
    (tmp_path / source.name).write_text(source.read_text() + "\n-- changed after applying\n")
    with pytest.raises(RuntimeError, match="checksum changed"):
        migrate(db, tmp_path)


def test_private_http_closed_loop_and_identity_boundary(store, owner, calendar):
    token = "integration-only-service-token-for-private-api"
    settings = Settings(
        _env_file=None, env="test", alpha_token=SecretStr(token), database_url=SecretStr(store.url)
    )
    client = TestClient(create_app(settings, calendar=calendar))
    headers = {"Authorization": f"Bearer {token}", "X-YTA-Owner-ID": str(owner)}
    request_id = str(uuid4())
    body = {"request_id": request_id, "symbol": "NVDA"}
    assert client.post("/internal/v1/prediction-drafts", json=body).status_code == 401
    first = client.post("/internal/v1/prediction-drafts", headers=headers, json=body)
    assert first.status_code == 200, first.text
    draft = first.json()
    assert client.post("/internal/v1/prediction-drafts", headers=headers, json=body).json() == draft
    other = {**headers, "X-YTA-Owner-ID": str(uuid4())}
    assert (
        client.get(f"/internal/v1/prediction-drafts/{draft['draft_id']}", headers=other).status_code
        == 404
    )
    freeze = {"draft_id": draft["draft_id"], "confirmed": True}
    keyed = {**headers, "Idempotency-Key": "confirmed-internal"}
    assert (
        client.post(
            "/internal/v1/predictions", headers=keyed, json={**freeze, "now": "2024-01-01"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/internal/v1/predictions", headers=keyed, json={**freeze, "confirmed": "true"}
        ).status_code
        == 422
    )
    frozen = client.post("/internal/v1/predictions", headers=keyed, json=freeze)
    assert frozen.status_code == 200, frozen.text
    pid = frozen.json()["prediction_id"]
    assert (
        client.post("/internal/v1/predictions", headers=keyed, json=freeze).json()["prediction_id"]
        == pid
    )
    assert client.get(f"/internal/v1/predictions/{pid}", headers=other).status_code == 404
    result = client.post(f"/internal/v1/replay/predictions/{pid}/settle", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["outcomes"][0]["result"]["actual_return"] == "0.020000000000"
    assert len(client.get("/internal/v1/predictions", headers=headers).json()["items"]) == 1
    live_settings = Settings(
        _env_file=None,
        env="test",
        data_mode="LIVE_RESEARCH",
        git_sha="a" * 40,
        alpha_token=SecretStr(token),
        database_url=SecretStr(store.url),
    )
    live = TestClient(create_app(live_settings, calendar=calendar))
    assert live.get("/internal/v1/market/snapshot", headers=headers).status_code == 503
    assert (
        live.post(f"/internal/v1/replay/predictions/{pid}/settle", headers=headers).status_code
        == 404
    )


def test_evaluation_pins_outcome_versions_and_separates_modes(db, store, owner, draft, calendar):
    from yta_alpha.evaluation import EvaluationRequest, create_report, get_report
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "evaluation", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    request = EvaluationRequest(
        data_mode=DataMode.REPLAY,
        base_from=snapshot.bars[-1].session,
        base_to=snapshot.bars[-1].session,
        cutoff=observed.as_of,
    )
    waiting = create_report(store, owner, request)
    assert waiting["counts"]["WAITING_DATA"] == 1
    assert waiting["mae"] is None
    early = create_report(store, owner, request.model_copy(update={"cutoff": snapshot.as_of}))
    assert early["counts"]["WAITING_TARGET"] == 1
    store.settle_prediction(owner, pid, observed, now=observed.as_of)
    first = create_report(store, owner, request)
    assert first["sample_count"] == first["scored_count"] == 1
    assert first["mae"] == "0.015000000000"
    assert first["zero_return_mae"] == "0.020000000000"
    assert first["direction_accuracy"] == "1.000000000000"
    assert first["manifest"][0]["settlement_version"] == 1
    assert create_report(store, owner, request) == first
    revised = replay_observation(calendar, "103")
    store.settle_prediction(owner, pid, revised, now=revised.as_of + timedelta(hours=1))
    assert create_report(store, owner, request) == first  # excludes later observation
    second = create_report(
        store, owner, request.model_copy(update={"cutoff": revised.as_of + timedelta(hours=1)})
    )
    assert second["manifest"][0]["settlement_version"] == 2
    assert second["mae"] == "0.025000000000"
    assert get_report(store, owner, UUID(first["report_id"])) == first
    assert get_report(store, owner, UUID(waiting["report_id"])) == waiting
    with pytest.raises(DomainError) as error:
        get_report(store, uuid4(), UUID(first["report_id"]))
    assert error.value.status == 404
    live = create_report(
        store, owner, request.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    )
    assert live["sample_count"] == 0 and live["mae"] is None
    with store._connect() as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("UPDATE yta.evaluation_reports SET payload=payload")
    with (
        psycopg.connect(db) as connection,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        connection.execute("DELETE FROM yta.evaluation_reports WHERE id=%s", (first["report_id"],))


def test_evaluation_includes_entire_cohort_and_deduplicates_concurrent_reports(store, owner, draft):
    from yta_alpha.evaluation import EvaluationRequest, create_report
    from yta_alpha.models import DataMode

    value, snapshot = draft
    for n in range(3):
        item = value if not n else store.create_draft(owner, snapshot, snapshot.as_of)
        store.freeze(
            owner, UUID(item["draft_id"]), f"cohort-{n}", confirmed=True, now=snapshot.as_of
        )
    request = EvaluationRequest(
        data_mode=DataMode.REPLAY,
        base_from=snapshot.bars[-1].session,
        base_to=snapshot.bars[-1].session,
        cutoff=snapshot.as_of,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(lambda _: create_report(store, owner, request), range(4)))
    assert len({r["report_id"] for r in reports}) == 1
    assert reports[0]["sample_count"] == 3
    assert reports[0]["base_session_counts"] == {str(snapshot.bars[-1].session): 3}
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE actor_id=%s "
                "AND action='evaluation.create'",
                (owner,),
            ).fetchone()["n"]
            == 1
        )


def test_worker_delayed_data_failure_backoff_and_recovery(store, owner, draft, calendar):
    from yta_alpha.jobs import SettlementWorker
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(owner, UUID(value["draft_id"]), "job", confirmed=True, now=snapshot.as_of)
    pid = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)

    class Provider:
        calls = 0

        def observation(self, forecast, now):
            self.calls += 1
            if self.calls == 1:
                return None
            if self.calls == 2:
                raise RuntimeError("private-provider-token-must-never-persist")
            return observed

    provider = Provider()
    worker = SettlementWorker(store, provider, DataMode.REPLAY)
    assert worker.tick(snapshot.as_of, owner=owner) == []
    assert provider.calls == 0
    first = worker.tick(observed.as_of, owner=owner)[0]
    assert first["status"] == "WAITING_DATA"
    assert worker.tick(observed.as_of, owner=owner) == []
    assert provider.calls == 1
    # A newly constructed worker respects the durable next-attempt time.
    restarted = SettlementWorker(store, provider, DataMode.REPLAY)
    second = restarted.tick(observed.as_of + timedelta(minutes=2), owner=owner)[0]
    assert second["status"] == "SETTLEMENT_FAILED"
    assert second["error_code"] == "PROVIDER_ERROR"
    assert store.get_prediction(owner, pid)["status"] == "SETTLEMENT_FAILED"
    third = restarted.tick(observed.as_of + timedelta(minutes=10), owner=owner)[0]
    assert third["status"] == "SETTLED" and third["attempt_number"] == 3
    assert restarted.tick(observed.as_of + timedelta(days=1), owner=owner) == []
    saved = store.get_prediction(owner, pid)
    assert len(saved["outcomes"]) == 1
    assert saved["forecast"] == frozen["forecast"]
    with store._connect() as connection:
        attempts = connection.execute(
            "SELECT * FROM yta.settlement_attempts WHERE prediction_id=%s ORDER BY attempt_number",
            (pid,),
        ).fetchall()
    assert [r["status"] for r in attempts] == ["WAITING_DATA", "SETTLEMENT_FAILED", "SETTLED"]
    assert "private-provider-token" not in str(attempts) + str(saved)
    with store._connect() as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("DELETE FROM yta.settlement_attempts WHERE prediction_id=%s", (pid,))


def test_worker_concurrent_lease_and_manual_settlement_race(store, owner, draft, calendar):
    from threading import Event

    from yta_alpha.jobs import SettlementWorker
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "race", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    entered, release = Event(), Event()

    class SlowProvider:
        calls = 0

        def observation(self, forecast, now):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            raise RuntimeError("upstream temporarily down")

    provider = SlowProvider()
    worker = SettlementWorker(store, provider, DataMode.REPLAY)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(worker.process, owner, pid, observed.as_of)
        assert entered.wait(5)
        try:
            assert worker.process(owner, pid, observed.as_of) is None
            store.settle_prediction(owner, pid, observed, now=observed.as_of)
        finally:
            release.set()
        assert running.result()["status"] == "SETTLED"
    assert provider.calls == 1
    saved = store.get_prediction(owner, pid)
    assert saved["status"] == "SETTLED"
    assert len(saved["outcomes"]) == 1
    assert not any(e["type"] == "SETTLEMENT_FAILED" for e in saved["events"])


def test_worker_mode_isolation_and_released_lease_recovery(store, owner, draft, calendar):
    from yta_alpha.jobs import ReplayProvider, SettlementWorker, UnavailableProvider
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "lease", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    at = replay_observation(calendar).as_of
    live = SettlementWorker(store, UnavailableProvider(), DataMode.LIVE_RESEARCH)
    assert live.tick(at, owner=owner) == []
    with pytest.raises(DomainError) as error:
        live.process(owner, pid, at)
    assert error.value.code == "COHORT_MISMATCH"
    replay = SettlementWorker(store, ReplayProvider(calendar), DataMode.REPLAY)
    # Simulate a worker dying after acquiring its connection-scoped lease.
    lease = store._connect()
    lease.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (f"settlement-worker:{pid}",))
    assert replay.process(owner, pid, at) is None
    lease.close()
    assert replay.process(owner, pid, at)["status"] == "SETTLED"


@pytest.fixture
def unscorable_evidence(calendar):
    from yta_alpha.resolution import UnscorableEvidence

    observed = replay_observation(calendar)
    return UnscorableEvidence(
        data_mode="REPLAY",
        target_session=observed.bars[0].session,
        reason="TARGET_HALTED_NO_CLOSE",
        provider="synthetic-exchange-notice",
        source_id="yta:fixture:target-halt",
        source_document_sha256="b" * 64,
        statement="Synthetic test notice confirms no eligible closing price for target session.",
        published_at=observed.as_of,
        available_at=observed.as_of,
        retrieved_at=observed.as_of,
    )


def test_unscorable_is_versioned_evidence_not_missing_price(
    db, store, owner, draft, calendar, unscorable_evidence
):
    from yta_alpha.evaluation import EvaluationRequest, create_report, get_report
    from yta_alpha.resolution import record_unscorable

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "unscorable", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    evidence = unscorable_evidence
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: record_unscorable(store, owner, pid, evidence, now=evidence.retrieved_at),
                range(4),
            )
        )
    assert all(len(r["outcomes"]) == 1 for r in results)
    outcome = results[0]["outcomes"][0]
    assert outcome["status"] == "UNSCORABLE"
    assert outcome["result"]["actual_return"] is None
    assert outcome["result"]["evidence"]["source_id"] == evidence.source_id
    assert results[0]["forecast"] == frozen["forecast"]
    request = EvaluationRequest(
        data_mode="REPLAY",
        base_from=snapshot.bars[-1].session,
        base_to=snapshot.bars[-1].session,
        cutoff=evidence.retrieved_at,
    )
    report = create_report(store, owner, request)
    assert report["counts"]["UNSCORABLE"] == 1 and report["scored_count"] == 0
    assert report["mae"] is None
    observed = replay_observation(calendar)
    corrected = store.settle_prediction(
        owner, pid, observed, now=observed.as_of + timedelta(hours=1)
    )
    assert [o["status"] for o in corrected["outcomes"]] == ["UNSCORABLE", "SETTLED"]
    assert get_report(store, owner, UUID(report["report_id"])) == report
    with pytest.raises(DomainError) as error:
        record_unscorable(
            store,
            owner,
            pid,
            evidence.model_copy(update={"source_id": "new-notice"}),
            now=observed.as_of + timedelta(hours=2),
        )
    assert error.value.code == "OUTCOME_VERSION_CONFLICT"
    updated = record_unscorable(
        store,
        owner,
        pid,
        evidence.model_copy(update={"source_id": "new-notice"}),
        now=observed.as_of + timedelta(hours=2),
        supersedes_version=2,
    )
    assert len(updated["outcomes"]) == 3
    assert updated["outcomes"][0] == outcome
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.outcome_evidence e JOIN yta.prediction_outcomes o "
                "ON o.id=e.outcome_id WHERE o.prediction_id=%s",
                (pid,),
            ).fetchone()["n"]
            == 2
        )
    with (
        psycopg.connect(db) as connection,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        connection.execute("UPDATE yta.settlement_evidence SET payload=payload")


def test_unscorable_rejects_foreign_future_and_premature_evidence(
    store, owner, draft, unscorable_evidence
):
    from yta_alpha.models import DataMode
    from yta_alpha.resolution import record_unscorable

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "evidence-boundary", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    evidence = unscorable_evidence
    cases = [
        (owner, evidence, snapshot.as_of, "NOT_DUE"),
        (uuid4(), evidence, evidence.retrieved_at, "NOT_FOUND"),
        (
            owner,
            evidence.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}),
            evidence.retrieved_at,
            "COHORT_MISMATCH",
        ),
        (
            owner,
            evidence.model_copy(
                update={"target_session": evidence.target_session + timedelta(days=1)}
            ),
            evidence.retrieved_at,
            "COHORT_MISMATCH",
        ),
        (
            owner,
            evidence.model_copy(update={"retrieved_at": evidence.retrieved_at + timedelta(days=1)}),
            evidence.retrieved_at,
            "FUTURE_INFORMATION",
        ),
        (
            owner,
            evidence.model_copy(
                update={"published_at": snapshot.as_of, "available_at": snapshot.as_of}
            ),
            evidence.retrieved_at,
            "INCOMPLETE_EVIDENCE",
        ),
    ]
    for actor, item, now, code in cases:
        with pytest.raises(DomainError) as error:
            record_unscorable(store, actor, pid, item, now=now)
        assert error.value.code == code
    assert store.get_prediction(owner, pid)["outcomes"] == []


def test_worker_records_unscorable_terminal_and_stops_polling(
    store, owner, draft, unscorable_evidence
):
    from yta_alpha.jobs import SettlementWorker
    from yta_alpha.models import DataMode

    value, snapshot = draft
    store.freeze(
        owner, UUID(value["draft_id"]), "unscorable-worker", confirmed=True, now=snapshot.as_of
    )

    class Provider:
        def observation(self, forecast, now):
            return unscorable_evidence

    worker = SettlementWorker(store, Provider(), DataMode.REPLAY)
    result = worker.tick(unscorable_evidence.retrieved_at, owner=owner)
    assert result[0]["status"] == "UNSCORABLE"
    assert result[0]["next_attempt_at"] is None
    assert worker.tick(unscorable_evidence.retrieved_at + timedelta(days=1), owner=owner) == []


def test_base_revision_versions_are_persisted_idempotently_and_reports_stay_fixed(
    db, store, owner, draft, calendar, unscorable_evidence
):
    from yta_alpha.evaluation import EvaluationRequest, create_report, get_report
    from yta_alpha.fixtures import replay_base_revision

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "base-revision", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    first = store.settle_prediction(owner, pid, observed, now=observed.as_of)
    request = EvaluationRequest(
        data_mode="REPLAY",
        base_from=snapshot.bars[-1].session,
        base_to=snapshot.bars[-1].session,
        cutoff=observed.as_of,
    )
    report = create_report(store, owner, request)
    revision = replay_base_revision(calendar)
    with pytest.raises(DomainError) as error:
        store.settle_prediction(owner, pid, observed, now=revision.as_of, base_revision=revision)
    assert error.value.code == "OUTCOME_VERSION_CONFLICT"
    with ThreadPoolExecutor(max_workers=4) as executor:
        versions = list(
            executor.map(
                lambda _: store.settle_prediction(
                    owner,
                    pid,
                    observed,
                    now=revision.as_of,
                    base_revision=revision,
                    supersedes_version=1,
                ),
                range(4),
            )
        )
    assert all(len(v["outcomes"]) == 2 for v in versions)
    result = versions[0]
    assert result["forecast"] == frozen["forecast"]
    assert result["outcomes"][0] == first["outcomes"][0]
    assert result["outcomes"][1]["result"]["base_price_difference"] == "1"
    assert result["outcomes"][1]["result"]["actual_return"] == "0.009900990099"
    assert get_report(store, owner, UUID(report["report_id"])) == report
    updated = create_report(store, owner, request.model_copy(update={"cutoff": revision.as_of}))
    assert updated["mae"] == "0.004900990099"
    assert updated["manifest"][0]["settlement_version"] == 2
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.outcome_base_revisions b JOIN "
                "yta.prediction_outcomes o ON o.id=b.outcome_id WHERE o.prediction_id=%s",
                (pid,),
            ).fetchone()["n"]
            == 1
        )
    with (
        psycopg.connect(db) as connection,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        connection.execute("UPDATE yta.outcome_base_revisions SET snapshot_hash=snapshot_hash")
    newer_target = replay_observation(calendar, "103")
    with pytest.raises(DomainError) as error:
        store.settle_prediction(owner, pid, newer_target, now=revision.as_of + timedelta(hours=1))
    assert error.value.code == "BASE_REVISION_REQUIRED"
    third = store.settle_prediction(
        owner,
        pid,
        newer_target,
        now=revision.as_of + timedelta(hours=1),
        base_revision=revision,
        supersedes_version=2,
    )
    assert len(third["outcomes"]) == 3
    assert third["outcomes"][-1]["result"]["base_close"] == "101"
    from yta_alpha.resolution import record_unscorable

    record_unscorable(
        store,
        owner,
        pid,
        unscorable_evidence,
        now=revision.as_of + timedelta(hours=2),
        supersedes_version=3,
    )
    with pytest.raises(DomainError) as error:
        store.settle_prediction(
            owner, pid, replay_observation(calendar, "104"), now=revision.as_of + timedelta(hours=3)
        )
    assert error.value.code == "BASE_REVISION_REQUIRED"


def test_base_revision_rejects_cross_provider_and_backdated_result(store, owner, draft, calendar):
    from yta_alpha.fixtures import replay_base_revision

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "revision-boundary", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    revision = replay_base_revision(calendar)
    store.settle_prediction(owner, pid, observed, now=revision.as_of + timedelta(days=1))
    with pytest.raises(DomainError) as error:
        store.settle_prediction(
            owner, pid, observed, now=revision.as_of, base_revision=revision, supersedes_version=1
        )
    assert error.value.code == "INVALID_TIME"
    with pytest.raises(DomainError) as error:
        store.settle_prediction(
            owner,
            pid,
            observed,
            now=revision.as_of + timedelta(days=2),
            base_revision=revision.model_copy(update={"provider": "other"}),
            supersedes_version=1,
        )
    assert error.value.code == "COHORT_MISMATCH"
    assert len(store.get_prediction(owner, pid)["outcomes"]) == 1


def test_alpha_readiness_checks_runtime_permissions_and_real_provider_gate(db, store, calendar):
    from yta_alpha.models import DataMode

    token = "readiness-integration-token-never-live-12345"
    headers = {"Authorization": f"Bearer {token}"}
    settings = Settings(
        _env_file=None,
        env="test",
        alpha_token=SecretStr(token),
        database_url=SecretStr(store.url),
        git_sha="a" * 40,
    )
    client = TestClient(create_app(settings, calendar=calendar))
    result = client.get("/internal/v1/ready", headers=headers)
    assert result.status_code == 200
    assert result.json()["checks"] == {"database": "ready", "market": "replay"}
    elevated = settings.model_copy(update={"database_url": SecretStr(db)})
    response = TestClient(create_app(elevated, calendar=calendar)).get(
        "/internal/v1/ready", headers=headers
    )
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "permissions_invalid"
    live = settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    response = TestClient(create_app(live, calendar=calendar)).get(
        "/internal/v1/ready", headers=headers
    )
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ready", "market": "unverified"}


def test_evaluation_http_auth_mode_ownership_and_retry(store, owner, draft, calendar):
    from yta_alpha.evaluation import EvaluationRequest, create_report

    value, snapshot = draft
    store.freeze(owner, UUID(value["draft_id"]), "report-http", confirmed=True, now=snapshot.as_of)
    token = "report-http-service-token-only-for-tests"
    settings = Settings(
        _env_file=None,
        env="test",
        data_mode="REPLAY",
        alpha_token=SecretStr(token),
        database_url=SecretStr(store.url),
    )
    client = TestClient(create_app(settings, calendar=calendar))
    path = "/internal/v1/evaluation-reports"
    body = dict(
        data_mode="REPLAY",
        base_from="2024-11-22",
        base_to="2024-11-22",
        cutoff="2024-12-03T00:00:00Z",
    )
    headers = {"Authorization": f"Bearer {token}", "X-YTA-Owner-ID": str(owner)}
    assert client.post(path, json=body).status_code == 401
    assert (
        client.post(path, json=body, headers={"Authorization": f"Bearer {token}"}).status_code
        == 422
    )
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["sample_count"] == 1
    assert report["counts"]["WAITING_DATA"] == 1
    assert report["mae"] is None
    assert client.post(path, json=body, headers=headers).json() == report
    detail = path + "/" + report["report_id"]
    assert client.get(detail, headers=headers).json() == report
    assert (
        client.get(detail, headers={**headers, "X-YTA-Owner-ID": str(uuid4())}).status_code == 404
    )
    assert (
        client.post(path, json={**body, "data_mode": "LIVE_RESEARCH"}, headers=headers).json()[
            "error"
        ]["code"]
        == "DATA_MODE_MISMATCH"
    )
    assert (
        client.post(path, json={**body, "owner_id": str(uuid4())}, headers=headers).status_code
        == 422
    )
    foreign = create_report(
        store, owner, EvaluationRequest(**{**body, "data_mode": "LIVE_RESEARCH"})
    )
    assert client.get(path + "/" + foreign["report_id"], headers=headers).status_code == 404


def test_report_pagination_ties_inserts_and_scope(store, owner, calendar):
    from datetime import UTC, datetime

    from psycopg.types.json import Jsonb
    from yta_alpha.evaluation import EvaluationRequest, create_report, list_reports
    from yta_alpha.forecast import content_hash
    from yta_alpha.models import DataMode

    request = EvaluationRequest(
        data_mode="REPLAY",
        base_from="2024-11-22",
        base_to="2024-11-22",
        cutoff="2024-12-03T00:00:00Z",
    )
    original = create_report(store, owner, request)
    ids = [UUID(original["report_id"])]
    # One transaction deliberately gives these rows the same created_at.
    with store._connect() as connection:
        for day in range(4, 9):
            payload = {k: v for k, v in original.items() if k not in ("report_id", "content_hash")}
            payload["cutoff"] = f"2024-12-{day:02d}T00:00:00Z"
            report_id = uuid4()
            connection.execute(
                "INSERT INTO yta.evaluation_reports(id,owner_id,content_hash,data_mode,payload) "
                "VALUES (%s,%s,%s,'REPLAY',%s)",
                (report_id, owner, content_hash(payload), Jsonb(payload)),
            )
            ids.append(report_id)
    first = list_reports(store, owner, DataMode.REPLAY, limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    assert all("manifest" not in row and "base_session_counts" not in row for row in first["items"])
    newer = create_report(store, owner, request.model_copy(update={"cutoff": datetime.now(UTC)}))
    found = [item["report_id"] for item in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = list_reports(store, owner, DataMode.REPLAY, cursor=UUID(cursor), limit=2)
        found.extend(item["report_id"] for item in page["items"])
        cursor = page["next_cursor"]
    assert len(found) == len(set(found)) == len(ids)
    assert set(found) == {str(value) for value in ids}
    assert (
        list_reports(store, owner, DataMode.REPLAY)["items"][0]["report_id"] == newer["report_id"]
    )
    assert list_reports(store, uuid4(), DataMode.REPLAY) == {"items": [], "next_cursor": None}
    assert list_reports(store, owner, DataMode.LIVE_RESEARCH)["items"] == []
    for foreign_owner, mode in [(uuid4(), DataMode.REPLAY), (owner, DataMode.LIVE_RESEARCH)]:
        with pytest.raises(DomainError) as error:
            list_reports(store, foreign_owner, mode, cursor=ids[0])
        assert error.value.status == 404
    token = "pagination-test-service-token-only-12345"
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                env="test",
                data_mode="REPLAY",
                alpha_token=SecretStr(token),
                database_url=SecretStr(store.url),
            ),
            calendar=calendar,
        )
    )
    path = "/internal/v1/evaluation-reports"
    headers = {"Authorization": f"Bearer {token}", "X-YTA-Owner-ID": str(owner)}
    assert client.get(path).status_code == 401
    assert len(client.get(path + "?limit=2", headers=headers).json()["items"]) == 2
    for query in ("limit=0", "limit=101", "cursor=invalid"):
        assert client.get(path + "?" + query, headers=headers).status_code == 422


def test_prediction_pages_and_global_status_filter(store, owner, calendar):
    snapshot = replay_snapshot(calendar)
    ids = []
    for index in range(7):
        draft = store.create_draft(owner, snapshot, snapshot.as_of)
        record = store.freeze(
            owner, UUID(draft["draft_id"]), f"page-{index}", confirmed=True, now=snapshot.as_of
        )
        ids.append(record["prediction_id"])
    first = store.prediction_page(owner, data_mode="REPLAY", limit=2)
    found = [item["prediction_id"] for item in first["items"]]
    cursor = first["next_cursor"]
    draft = store.create_draft(owner, snapshot, snapshot.as_of)
    newer = store.freeze(
        owner,
        UUID(draft["draft_id"]),
        "new-page",
        confirmed=True,
        now=snapshot.as_of + timedelta(seconds=1),
    )
    while cursor:
        page = store.prediction_page(owner, data_mode="REPLAY", cursor=UUID(cursor), limit=2)
        found.extend(item["prediction_id"] for item in page["items"])
        cursor = page["next_cursor"]
    assert len(found) == len(set(found)) == 7
    assert set(found) == set(ids)
    assert (
        store.prediction_page(owner, data_mode="REPLAY")["items"][0]["prediction_id"]
        == newer["prediction_id"]
    )
    observation = replay_observation(calendar)
    store.settle_prediction(owner, UUID(found[-1]), observation, now=observation.as_of)
    settled = store.prediction_page(owner, data_mode="REPLAY", status="SETTLED", limit=1)
    assert [row["prediction_id"] for row in settled["items"]] == [found[-1]]
    assert settled["next_cursor"] is None
    assert len(store.prediction_page(owner, data_mode="REPLAY", status="FROZEN")["items"]) == 7
    for user, mode in [(uuid4(), "REPLAY"), (owner, "LIVE_RESEARCH")]:
        assert store.prediction_page(user, data_mode=mode)["items"] == []
        with pytest.raises(DomainError) as error:
            store.prediction_page(user, data_mode=mode, cursor=UUID(ids[0]))
        assert error.value.status == 404
    token = "prediction-pagination-test-token-12345"
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                env="test",
                data_mode="REPLAY",
                alpha_token=SecretStr(token),
                database_url=SecretStr(store.url),
            ),
            calendar=calendar,
        )
    )
    headers = {"Authorization": f"Bearer {token}", "X-YTA-Owner-ID": str(owner)}
    path = "/internal/v1/predictions"
    assert client.get(path).status_code == 401
    assert len(client.get(path + "?limit=2", headers=headers).json()["items"]) == 2
    for query in ("limit=0", "limit=101", "status=invalid", "cursor=bad"):
        assert client.get(path + "?" + query, headers=headers).status_code == 422


def test_corrections_preserve_original_deduplicate_and_count_in_reports(store, owner, draft):
    from yta_alpha.evaluation import EvaluationRequest, create_report, get_report

    value, snapshot = draft
    original = store.freeze(
        owner, UUID(value["draft_id"]), "original-correction", confirmed=True, now=snapshot.as_of
    )
    original_id = UUID(original["prediction_id"])
    request = EvaluationRequest(
        data_mode="REPLAY",
        base_from="2024-11-22",
        base_to="2024-11-22",
        cutoff="2024-12-03T00:00:00Z",
    )
    old_report = create_report(store, owner, request)
    replacement = store.create_draft(owner, snapshot, snapshot.as_of)
    args = (owner, UUID(replacement["draft_id"]), "correction-key")

    def correct(_):
        return store.freeze(
            *args, confirmed=True, now=snapshot.as_of, supersedes_prediction_id=original_id
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(correct, range(4)))
    assert len({row["prediction_id"] for row in results}) == 1
    corrected = results[0]
    assert corrected["supersedes_prediction_id"] == str(original_id)
    assert store.get_prediction(owner, original_id) == original
    assert (
        store.freeze(
            *args,
            confirmed=True,
            now=snapshot.as_of + timedelta(days=5),
            supersedes_prediction_id=original_id,
        )
        == corrected
    )
    with pytest.raises(DomainError) as error:
        store.freeze(*args, confirmed=True, now=snapshot.as_of)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            UUID(replacement["draft_id"]),
            "different-key",
            confirmed=True,
            now=snapshot.as_of,
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    report = create_report(store, owner, request)
    assert report["sample_count"] == 2 and report["correction_count"] == 1
    assert report["base_session_counts"] == {"2024-11-22": 2}
    assert {row["prediction_id"] for row in report["manifest"]} == {
        str(original_id),
        corrected["prediction_id"],
    }
    assert get_report(store, owner, UUID(old_report["report_id"])) == old_report
    with store._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE yta.predictions SET supersedes_prediction_id=NULL WHERE id=%s",
                (UUID(corrected["prediction_id"]),),
            )


def test_corrections_require_owner_confirmation_and_original_window(
    db, store, owner, draft, calendar
):
    value, snapshot = draft
    original = store.freeze(
        owner, UUID(value["draft_id"]), "correction-rules", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(original["prediction_id"])
    next_draft = store.create_draft(owner, snapshot, snapshot.as_of)
    did = UUID(next_draft["draft_id"])
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            did,
            "unconfirmed",
            confirmed=False,
            now=snapshot.as_of,
            supersedes_prediction_id=pid,
        )
    assert error.value.code == "CONFIRMATION_REQUIRED"
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            did,
            "expired",
            confirmed=True,
            now=snapshot.as_of + timedelta(days=4),
            supersedes_prediction_id=pid,
        )
    assert error.value.code == "DRAFT_EXPIRED"
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            did,
            "missing-parent",
            confirmed=True,
            now=snapshot.as_of,
            supersedes_prediction_id=uuid4(),
        )
    assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        store.freeze(
            uuid4(),
            did,
            "foreign-owner",
            confirmed=True,
            now=snapshot.as_of,
            supersedes_prediction_id=pid,
        )
    assert error.value.status == 404
    stranger = uuid4()
    with psycopg.connect(db) as connection:
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES (%s,%s)", (stranger, f"{stranger}@test.invalid")
        )
    stranger_draft = store.create_draft(stranger, snapshot, snapshot.as_of)
    with pytest.raises(DomainError) as error:
        store.freeze(
            stranger,
            UUID(stranger_draft["draft_id"]),
            "foreign-parent",
            confirmed=True,
            now=snapshot.as_of,
            supersedes_prediction_id=pid,
        )
    assert error.value.status == 404
    from yta_alpha.models import DataMode

    live_draft = store.create_draft(
        owner, snapshot.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), snapshot.as_of
    )
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            UUID(live_draft["draft_id"]),
            "foreign-mode",
            confirmed=True,
            now=snapshot.as_of,
            supersedes_prediction_id=pid,
        )
    assert error.value.code == "INVALID_CORRECTION"
    # A later valid draft cannot extend the original prediction's correction window.
    next_session = calendar.after(snapshot.bars[-1].session, 1)
    at = calendar.close_at(next_session) + timedelta(minutes=10)
    bar = snapshot.bars[-1].model_copy(
        update={"session": next_session, "available_at": at, "retrieved_at": at}
    )
    later = snapshot.model_copy(
        update={
            "bars": snapshot.bars + (bar,),
            "as_of": at,
            "actions_verified_through": next_session,
        }
    )
    later_draft = store.create_draft(owner, later, later.as_of)
    with pytest.raises(DomainError) as error:
        store.freeze(
            owner,
            UUID(later_draft["draft_id"]),
            "later-base",
            confirmed=True,
            now=later.as_of,
            supersedes_prediction_id=pid,
        )
    assert error.value.code == "INVALID_CORRECTION"


def test_correction_http_confirmation_and_ownership(store, owner, draft, calendar):
    value, snapshot = draft
    original = store.freeze(
        owner, UUID(value["draft_id"]), "http-original", confirmed=True, now=snapshot.as_of
    )
    replacement = store.create_draft(owner, snapshot, snapshot.as_of)
    token = "correction-service-token-only-for-tests-12345"
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                env="test",
                data_mode="REPLAY",
                alpha_token=SecretStr(token),
                database_url=SecretStr(store.url),
            ),
            calendar=calendar,
        )
    )
    path = "/internal/v1/predictions/" + original["prediction_id"] + "/corrections"
    headers = {
        "Authorization": "Bearer " + token,
        "X-YTA-Owner-ID": str(owner),
        "Idempotency-Key": "correction-http",
    }
    body = {"draft_id": replacement["draft_id"], "confirmed": True}
    assert client.post(path, json=body).status_code == 401
    assert (
        client.post(
            path, json=body, headers={**headers, "X-YTA-Owner-ID": str(uuid4())}
        ).status_code
        == 404
    )
    assert (
        client.post(path, json={**body, "confirmed": False}, headers=headers).json()["error"][
            "code"
        ]
        == "CONFIRMATION_REQUIRED"
    )
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["supersedes_prediction_id"] == original["prediction_id"]
    assert client.post(path, json=body, headers=headers).json() == response.json()


def test_worker_process_health_sigterm_and_restart(store, owner, tmp_path):
    import json
    import subprocess
    import sys
    import time
    from datetime import UTC, datetime

    from yta_alpha.worker_health import probe

    path = tmp_path / "worker-health.json"
    env = {
        **os.environ,
        "YTA_ENV": "test",
        "YTA_DATA_MODE": "REPLAY",
        "YTA_DATABASE_URL": store.url,
        "YTA_ALPHA_TOKEN": "worker-test-token-not-a-real-secret",
    }
    command = [
        sys.executable,
        "-m",
        "yta_alpha.jobs",
        "--watch",
        "--interval",
        "600",
        "--owner",
        str(owner),
        "--replay-at",
        "2024-12-03T00:00:00Z",
        "--heartbeat-file",
        str(path),
    ]
    run_ids = []
    for graceful in (True, False):
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if probe(path, "REPLAY", 60)["status"] == "ready":
                    body = json.loads(path.read_text())
                    if body["pid"] == process.pid:
                        break
                assert process.poll() is None
                time.sleep(0.02)
            else:
                pytest.fail("Worker did not complete a real database batch")
            run_ids.append(body["run_id"])
            if graceful:
                process.terminate()
                stdout, stderr = process.communicate(timeout=3)
                assert process.returncode == 0
                assert stderr == b""
                assert json.loads(stdout)["results"] == []
                assert json.loads(path.read_text())["state"] == "stopped"
                assert probe(path, "REPLAY", 60)["status"] == "not_ready"
            else:
                process.kill()
                process.communicate(timeout=3)
                after_expiry = datetime.now(UTC) + timedelta(seconds=61)
                assert probe(path, "REPLAY", 60, now=after_expiry)["reason"] == "stale_heartbeat"
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
    assert run_ids[0] != run_ids[1]


def test_worker_cli_redacts_database_failure(tmp_path):
    import json
    import subprocess
    import sys

    path = tmp_path / "worker-health.json"
    env = {
        **os.environ,
        "YTA_ENV": "test",
        "YTA_DATA_MODE": "REPLAY",
        "YTA_DATABASE_URL": "postgresql://private-user:private-password@127.0.0.1:1/worker_test",
        "YTA_ALPHA_TOKEN": "worker-test-token-not-a-real-secret",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.jobs",
            "--replay-at",
            "2024-12-03T00:00:00Z",
            "--heartbeat-file",
            str(path),
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(result.stderr) == {"error_code": "WORKER_FAILED"}
    assert result.stdout == b""
    assert json.loads(path.read_text())["state"] == "failed"


def test_queue_metrics_cover_backoff_failures_terminal_results_and_scope(
    store, owner, calendar, unscorable_evidence
):
    from datetime import datetime

    from yta_alpha.jobs import ReplayProvider, SettlementWorker, UnavailableProvider
    from yta_alpha.models import DataMode
    from yta_alpha.resolution import record_unscorable

    snapshot = replay_snapshot(calendar)
    observed = replay_observation(calendar)
    ids = []
    for i in range(5):
        draft = store.create_draft(owner, snapshot, snapshot.as_of)
        saved = store.freeze(
            owner, UUID(draft["draft_id"]), f"queue-{i}", confirmed=True, now=snapshot.as_of
        )
        ids.append(UUID(saved["prediction_id"]))
    ready = SettlementWorker(store, ReplayProvider(calendar), DataMode.REPLAY)
    failing = SettlementWorker(store, UnavailableProvider(), DataMode.REPLAY)

    class Delayed:
        def observation(self, forecast, now):
            return None

    waiting = SettlementWorker(store, Delayed(), DataMode.REPLAY)
    assert ready.queue_status(snapshot.as_of, owner=owner)["due_count"] == 0
    with pytest.raises(DomainError):
        ready.queue_status(datetime(2024, 12, 3), owner=owner)
    waiting.process(owner, ids[1], observed.as_of)
    failing.process(owner, ids[2], observed.as_of)
    ready.process(owner, ids[3], observed.as_of)
    record_unscorable(
        store, owner, ids[4], unscorable_evidence, now=unscorable_evidence.retrieved_at
    )
    before = [store.get_prediction(owner, pid) for pid in ids]
    queue = ready.queue_status(observed.as_of, owner=owner)
    assert {
        key: queue[key]
        for key in (
            "due_count",
            "eligible_count",
            "backoff_count",
            "waiting_data_count",
            "failed_count",
            "unattempted_count",
        )
    } == {
        "due_count": 3,
        "eligible_count": 1,
        "backoff_count": 2,
        "waiting_data_count": 1,
        "failed_count": 1,
        "unattempted_count": 1,
    }
    target = datetime.fromisoformat(saved["forecast"]["target_close_at"])
    assert datetime.fromisoformat(queue["oldest_due_at"]) == target
    assert queue["oldest_overdue_seconds"] == int((observed.as_of - target).total_seconds())
    later = ready.queue_status(observed.as_of + timedelta(minutes=10), owner=owner)
    assert later["eligible_count"] == 3 and later["backoff_count"] == 0
    assert later["waiting_data_count"] == later["failed_count"] == 1
    assert [store.get_prediction(owner, pid) for pid in ids] == before
    assert ready.queue_status(observed.as_of, owner=uuid4())["due_count"] == 0
    live = SettlementWorker(store, UnavailableProvider(), DataMode.LIVE_RESEARCH)
    empty = live.queue_status(observed.as_of, owner=owner)
    assert empty["due_count"] == 0
    assert empty["oldest_due_at"] is empty["oldest_overdue_seconds"] is None
    failing.process(owner, ids[1], observed.as_of + timedelta(minutes=10))
    changed = ready.queue_status(observed.as_of + timedelta(minutes=10), owner=owner)
    assert changed["waiting_data_count"] == 0 and changed["failed_count"] == 2


def test_queue_only_cli_reads_pending_facts_without_settling(store, owner, draft, calendar):
    import json
    import subprocess
    import sys

    value, snapshot = draft
    saved = store.freeze(
        owner, UUID(value["draft_id"]), "queue-readonly", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(saved["prediction_id"])
    before = store.get_prediction(owner, pid)
    env = {
        **os.environ,
        "YTA_ENV": "test",
        "YTA_DATA_MODE": "REPLAY",
        "YTA_DATABASE_URL": store.url,
        "YTA_ALPHA_TOKEN": "worker-test-token-not-a-real-secret",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.jobs",
            "--queue-only",
            "--owner",
            str(owner),
            "--replay-at",
            replay_observation(calendar).as_of.isoformat(),
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    queue = json.loads(result.stdout)
    assert queue["due_count"] == queue["eligible_count"] == queue["unattempted_count"] == 1
    assert queue["scope"] == "owner" and queue["data_mode"] == "REPLAY"
    assert str(pid) not in result.stdout.decode() and str(owner) not in result.stdout.decode()
    assert store.get_prediction(owner, pid) == before


def test_migration_catalog_cannot_omit_history_and_failed_batch_rolls_back(db, tmp_path):
    import shutil

    for source in (ROOT / "infra/migrations").glob("*.sql"):
        shutil.copyfile(source, tmp_path / source.name)
    last = sorted(tmp_path.glob("*.sql"))[-1]
    last.unlink()
    with pytest.raises(RuntimeError, match="omits applied"):
        migrate(db, tmp_path)
    shutil.copyfile(ROOT / "infra/migrations" / last.name, last)
    table = "migration_rollback_" + uuid4().hex
    (tmp_path / "999_test_atomic.sql").write_text(
        f"CREATE TABLE yta.{table}(id integer); SELECT 1/0;"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(db, tmp_path)
    with psycopg.connect(db) as connection:
        assert connection.execute("SELECT to_regclass(%s)", (f"yta.{table}",)).fetchone()[0] is None
        assert (
            connection.execute(
                "SELECT count(*) FROM public.yta_migrations WHERE name='999_test_atomic.sql'"
            ).fetchone()[0]
            == 0
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: migrate(db, ROOT / "infra/migrations"), range(2)))
    assert results == [[], []]


def test_freeze_backend_termination_before_commit_rolls_back_all_facts(
    db, store, owner, draft, monkeypatch
):
    value, snapshot = draft
    original = store._connect

    @contextmanager
    def interrupted_connection():
        with original() as connection:
            yield connection
            # Kill only this test's runtime backend after statements, before COMMIT.
            with psycopg.connect(db, autocommit=True) as admin:
                assert admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE pid=%s AND datname=current_database() AND usename='yta_app'",
                    (connection.info.backend_pid,),
                ).fetchone() == (True,)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted_connection)
        with pytest.raises(psycopg.OperationalError):
            store.freeze(
                owner,
                UUID(value["draft_id"]),
                "terminated-freeze",
                confirmed=True,
                now=snapshot.as_of,
            )
    with original() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.predictions WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.idempotency_records WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE actor_id=%s "
                "AND action='prediction.freeze'",
                (owner,),
            ).fetchone()["n"]
            == 0
        )
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "terminated-freeze", confirmed=True, now=snapshot.as_of
    )
    assert frozen["status"] == "FROZEN" and len(frozen["events"]) == 1


@contextmanager
def _drop_postgres_commit_ack(runtime_url):
    """Loopback TCP relay drops the actual PostgreSQL COMMIT response, after commit."""
    config = conninfo_to_dict(runtime_url)
    assert config["dbname"].endswith("_test")
    assert config.get("host") in {"127.0.0.1", "localhost"}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    dropped = threading.Event()
    errors = []

    def relay():
        try:
            with (
                listener.accept()[0] as client,
                socket.create_connection(
                    (config["host"], int(config.get("port", 5432))), timeout=10
                ) as upstream,
            ):
                client.settimeout(10)

                def forward():
                    try:
                        while chunk := client.recv(65536):
                            upstream.sendall(chunk)
                    except OSError:
                        pass

                sender = threading.Thread(target=forward, daemon=True)
                sender.start()

                def read_exact(length):
                    buffer = bytearray()
                    while len(buffer) < length:
                        chunk = upstream.recv(length - len(buffer))
                        if not chunk:
                            raise EOFError("PostgreSQL response ended before commit")
                        buffer.extend(chunk)
                    return bytes(buffer)

                try:
                    while True:
                        header = read_exact(5)
                        length = int.from_bytes(header[1:], "big")
                        assert 4 <= length <= 16 * 1024 * 1024
                        payload = read_exact(length - 4)
                        if header[:1] == b"C" and payload == b"COMMIT\x00":
                            dropped.set()
                            client.shutdown(socket.SHUT_RDWR)
                            break
                        client.sendall(header + payload)
                finally:
                    sender.join(2)
        except Exception as error:
            errors.append(type(error).__name__)

    thread = threading.Thread(target=relay, daemon=True)
    thread.start()
    proxied = make_conninfo(
        runtime_url,
        host="127.0.0.1",
        port=listener.getsockname()[1],
        sslmode="disable",
        gssencmode="disable",
    )
    try:
        yield proxied, dropped
    finally:
        listener.close()
        thread.join(12)
        assert not thread.is_alive() and not errors, errors


def test_freeze_lost_commit_ack_replays_original_after_expiry(store, owner, draft, calendar):
    value, snapshot = draft
    key = "lost-commit-ack"
    with _drop_postgres_commit_ack(store.url) as (url, dropped):
        uncertain = ResearchStore(url, calendar, "a" * 40)
        with pytest.raises(psycopg.OperationalError):
            uncertain.freeze(
                owner, UUID(value["draft_id"]), key, confirmed=True, now=snapshot.as_of
            )
        assert dropped.is_set(), "Must drop a real committed transaction acknowledgement"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT id FROM yta.predictions WHERE owner_id=%s", (owner,)
        ).fetchall()
        assert len(row) == 1
        prediction_id = row[0]["id"]
    replay = store.freeze(
        owner,
        UUID(value["draft_id"]),
        key,
        confirmed=True,
        now=snapshot.as_of + timedelta(days=100),
    )
    assert UUID(replay["prediction_id"]) == prediction_id
    assert len(replay["events"]) == 1
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.idempotency_records WHERE owner_id=%s "
                "AND operation='freeze' AND key=%s",
                (owner, key),
            ).fetchone()["n"]
            == 1
        )

        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE actor_id=%s "
                "AND action='prediction.freeze'",
                (owner,),
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.predictions WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 1
        )


def test_settlement_termination_before_commit_preserves_frozen_forecast(
    db, store, owner, draft, calendar, monkeypatch
):
    value, snapshot = draft
    frozen = store.freeze(
        owner,
        UUID(value["draft_id"]),
        "settlement-interruption",
        confirmed=True,
        now=snapshot.as_of,
    )
    prediction_id = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)
    original = store._connect

    @contextmanager
    def interrupted():
        with original() as connection:
            yield connection
            with psycopg.connect(db, autocommit=True) as admin:
                assert admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE pid=%s AND datname=current_database() AND usename='yta_app'",
                    (connection.info.backend_pid,),
                ).fetchone() == (True,)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted)
        with pytest.raises(psycopg.OperationalError):
            store.settle_prediction(owner, prediction_id, observed, now=observed.as_of)
    unchanged = store.get_prediction(owner, prediction_id)
    assert unchanged == frozen
    with original() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s "
                "AND action='prediction.settle'",
                (str(prediction_id),),
            ).fetchone()["n"]
            == 0
        )
    retry = store.settle_prediction(owner, prediction_id, observed, now=observed.as_of)
    assert retry["forecast"] == frozen["forecast"]
    assert len(retry["outcomes"]) == 1 and retry["outcomes"][0]["settlement_version"] == 1


def test_worker_reconciles_committed_settlement_after_lost_ack(
    store, owner, draft, calendar, monkeypatch
):
    from yta_alpha.jobs import SettlementWorker
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "worker-lost-ack", confirmed=True, now=snapshot.as_of
    )
    prediction_id = UUID(frozen["prediction_id"])
    observed = replay_observation(calendar)

    class Provider:
        calls = 0

        def observation(self, _forecast, _now):
            self.calls += 1
            return observed

    provider = Provider()
    with _drop_postgres_commit_ack(store.url) as (url, dropped):
        uncertain = ResearchStore(url, calendar, "a" * 40)
        with monkeypatch.context() as patch:
            # Only the settlement transaction goes through the real TCP fault;
            # the worker's lease and reconciliation connections remain available.
            patch.setattr(store, "settle_prediction", uncertain.settle_prediction)
            result = SettlementWorker(store, provider, DataMode.REPLAY).process(
                owner, prediction_id, observed.as_of
            )
        assert dropped.is_set()
    assert result["status"] == "SETTLED"
    assert result["error_code"] is None and result["next_attempt_at"] is None
    saved = store.get_prediction(owner, prediction_id)
    assert saved["forecast"] == frozen["forecast"]
    assert len(saved["outcomes"]) == 1
    assert [event["type"] for event in saved["events"]] == ["FROZEN", "SETTLED"]
    replay = store.settle_prediction(owner, prediction_id, observed, now=observed.as_of)
    assert replay == saved
    assert (
        SettlementWorker(store, provider, DataMode.REPLAY).tick(
            observed.as_of + timedelta(days=1), owner=owner
        )
        == []
    )
    assert provider.calls == 1
    with store._connect() as connection:
        attempts = connection.execute(
            "SELECT status,error_code,next_attempt_at FROM yta.settlement_attempts "
            "WHERE prediction_id=%s",
            (prediction_id,),
        ).fetchall()
        assert attempts == [{"status": "SETTLED", "error_code": None, "next_attempt_at": None}]
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events WHERE resource_id=%s "
                "AND action='prediction.settle'",
                (str(prediction_id),),
            ).fetchone()["n"]
            == 1
        )


def test_symbol_history_and_private_http_authorization(store, calendar):
    from yta_alpha.symbols import symbol_history

    history = symbol_history(store.url, " nvda ")
    assert history.symbol == "NVDA" and history.qualification_status == "UNVERIFIED"
    assert history.versions[0].status == "UNVERIFIED"
    assert history.versions[0].effective_from is None
    assert history.versions[0].source_id == "yta:mvp-scope"
    token = "symbol-service-token-fixture-123456789"
    tools_token = "symbol-tools-token-distinct-123456789"
    app = create_app(
        Settings(
            _env_file=None, alpha_token=token, alpha_tools_token=tools_token, database_url=store.url
        ),
        calendar=calendar,
    )
    with TestClient(app) as client:
        assert client.get("/internal/v1/symbols/NVDA").status_code == 401
        assert (
            client.get(
                "/internal/v1/symbols/NVDA", headers={"Authorization": "Bearer " + tools_token}
            ).status_code
            == 401
        )
        headers = {"Authorization": "Bearer " + token}
        response = client.get("/internal/v1/symbols/nvda", headers=headers)
        assert response.status_code == 200 and response.json()["symbol"] == "NVDA"
        for symbol, code in [("AAPL", "UNSUPPORTED_SCOPE"), ("NV%20DA", "INVALID_SYMBOL")]:
            response = client.get("/internal/v1/symbols/" + symbol, headers=headers)
            assert response.status_code == 422 and response.json()["error"]["code"] == code


def test_symbol_history_constraints_and_immutable_permissions(db, store):
    symbol = "YTA" + uuid4().hex[:6].upper()
    with psycopg.connect(db) as owner_connection:
        with owner_connection.transaction(force_rollback=True):
            for version, status, successor, effective in [
                (1, "ACTIVE", None, "2020-01-01"),
                (2, "RENAMED", "NEWYTA", "2021-01-01"),
                (3, "DELISTED", None, "2022-01-01"),
            ]:
                owner_connection.execute(
                    "INSERT INTO yta.symbols(id,symbol,version,status,successor_symbol,"
                    "effective_from,source_id) VALUES (%s,%s,%s,%s,%s,%s,'fixture-only')",
                    (uuid4(), symbol, version, status, successor, effective),
                )
            rows = owner_connection.execute(
                "SELECT version,status,successor_symbol FROM yta.symbols WHERE symbol=%s "
                "ORDER BY version",
                (symbol,),
            ).fetchall()
            assert rows == [(1, "ACTIVE", None), (2, "RENAMED", "NEWYTA"), (3, "DELISTED", None)]
            with pytest.raises(psycopg.errors.CheckViolation), owner_connection.transaction():
                owner_connection.execute(
                    "INSERT INTO yta.symbols(id,symbol,version,status,source_id) "
                    "VALUES (%s,'bad symbol',1,'UNVERIFIED','fixture-only')",
                    (uuid4(),),
                )
            for statement in [
                "UPDATE yta.symbols SET symbol=symbol WHERE symbol='NVDA'",
                "DELETE FROM yta.symbols WHERE symbol='NVDA'",
                "TRUNCATE yta.symbols",
            ]:
                with (
                    pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                    owner_connection.transaction(),
                ):
                    owner_connection.execute(statement)
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.symbols WHERE symbol=%s", (symbol,)
            ).fetchone()["n"]
            == 0
        )
    for statement in [
        "UPDATE yta.symbols SET symbol=symbol WHERE symbol='NVDA'",
        "DELETE FROM yta.symbols WHERE symbol='NVDA'",
        "TRUNCATE yta.symbols",
    ]:
        with store._connect() as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


def test_draft_evidence_is_historical_owned_and_mode_scoped(store, owner, draft):
    from yta_alpha.models import DataMode

    value, snapshot = draft
    draft_id = UUID(value["draft_id"])
    evidence = store.draft_evidence(owner, draft_id, DataMode.REPLAY)
    assert evidence.snapshot == snapshot
    assert evidence.data_snapshot_hash == value["forecast"]["data_snapshot_hash"]
    # Recovery uses stored facts, not the current provider or the draft expiry clock.
    restored = ResearchStore(store.url, store.calendar, "b" * 40)
    assert restored.draft_evidence(owner, draft_id, DataMode.REPLAY) == evidence
    for requested_owner, requested_id, mode in [
        (uuid4(), draft_id, DataMode.REPLAY),
        (owner, uuid4(), DataMode.REPLAY),
        (owner, draft_id, DataMode.LIVE_RESEARCH),
    ]:
        with pytest.raises(DomainError) as error:
            store.draft_evidence(requested_owner, requested_id, mode)
        assert error.value.code == "NOT_FOUND"


@pytest.mark.parametrize("damage", ["hash", "invalid_payload", "as_of"])
def test_draft_evidence_rejects_inconsistent_stored_facts(
    isolated_evidence_store, calendar, damage
):
    store, owner = isolated_evidence_store
    original = replay_snapshot(calendar)
    draft = store.create_draft(owner, original, original.as_of), original
    from psycopg.types.json import Jsonb
    from yta_alpha.models import DataMode

    value, snapshot = draft
    payload = snapshot.model_dump(mode="json")
    saved = dict(value["forecast"])
    digest = uuid4().hex + uuid4().hex
    if damage == "invalid_payload":
        payload["bars"] = []
    elif damage == "as_of":
        digest = value["forecast"]["data_snapshot_hash"]
        saved["as_of"] = (snapshot.as_of - timedelta(seconds=1)).isoformat()
    saved["data_snapshot_hash"] = digest
    identity = uuid4()
    with store._connect() as connection:
        if damage != "as_of":
            connection.execute(
                "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) "
                "VALUES(%s,%s,%s,%s)",
                (digest, "REPLAY", snapshot.provider, Jsonb(payload)),
            )
        connection.execute(
            "INSERT INTO yta.prediction_drafts "
            "(id,owner_id,data_snapshot_hash,model_version,forecast,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (identity, owner, digest, saved["model_version"], Jsonb(saved), saved["expires_at"]),
        )
    with pytest.raises(DomainError) as error:
        store.draft_evidence(owner, identity, DataMode.REPLAY)
    assert error.value.code == "EVIDENCE_INVALID"
    assert error.value.status == 503


def test_draft_evidence_api_auth_serialization_and_no_mutation(store, owner, draft, calendar):
    from yta_alpha.models import DataMode

    value, snapshot = draft
    service_token = "evidence-service-token-not-a-real-secret"
    tools_token = "evidence-tools-token-not-a-real-secret"
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=SecretStr(store.url),
        alpha_token=SecretStr(service_token),
        alpha_tools_token=SecretStr(tools_token),
    )
    path = f"/internal/v1/prediction-drafts/{value['draft_id']}/evidence"
    headers = {"Authorization": f"Bearer {service_token}", "X-Yta-Owner-Id": str(owner)}
    with TestClient(create_app(settings, calendar=calendar)) as client:
        assert client.get(path).status_code == 401
        assert (
            client.get(
                path, headers={**headers, "Authorization": f"Bearer {tools_token}"}
            ).status_code
            == 401
        )
        assert (
            client.get(path, headers={"Authorization": f"Bearer {service_token}"}).status_code
            == 422
        )
        assert (
            client.get(path, headers={**headers, "X-Yta-Owner-Id": str(uuid4())}).status_code == 404
        )
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"] == snapshot.model_dump(mode="json")
        assert isinstance(body["snapshot"]["bars"][0]["close"], str)
        assert body["data_snapshot_hash"] == value["forecast"]["data_snapshot_hash"]
    with TestClient(
        create_app(
            settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), calendar=calendar
        )
    ) as client:
        assert client.get(path, headers=headers).status_code == 404
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.predictions WHERE owner_id=%s", (owner,)
            ).fetchone()["n"]
            == 0
        )


def test_snapshot_evidence_index_is_atomic_and_idempotent(db, store, owner, calendar):
    from yta_alpha.forecast import snapshot_hash

    snapshot = replay_snapshot(calendar).model_copy(update={"source_id": f"evidence-{uuid4()}"})
    digest = snapshot_hash(snapshot)
    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(
            pool.map(lambda _: store.create_draft(owner, snapshot, snapshot.as_of), range(4))
        )
    assert all(item["forecast"]["data_snapshot_hash"] == digest for item in saved)
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM yta.evidence_items WHERE content_hash=%s", (digest,)
        ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["source_id"] == snapshot.source_id
        assert row["data_mode"] == "REPLAY"
        assert row["available_at"] == max(bar.available_at for bar in snapshot.bars)
        assert row["as_of"] == snapshot.as_of
        assert row["kind"] == "MARKET_SNAPSHOT"
    for action in [
        "UPDATE yta.evidence_items SET source_id=source_id",
        "DELETE FROM yta.evidence_items",
        "TRUNCATE yta.evidence_items",
    ]:
        with store._connect() as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(action)
        with (
            psycopg.connect(db) as connection,
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        ):
            connection.execute(action)


def test_snapshot_and_evidence_index_rollback_together(store, calendar):
    from yta_alpha.forecast import snapshot_hash

    snapshot = replay_snapshot(calendar).model_copy(update={"source_id": f"rollback-{uuid4()}"})
    digest = snapshot_hash(snapshot)
    with pytest.raises(RuntimeError, match="abort transaction"), store._connect() as connection:
        store._save_snapshot(connection, snapshot)
        raise RuntimeError("abort transaction")
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT hash FROM yta.data_snapshots WHERE hash=%s", (digest,)
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT content_hash FROM yta.evidence_items WHERE content_hash=%s", (digest,)
            ).fetchone()
            is None
        )


def test_conflicting_evidence_index_prevents_new_draft(isolated_evidence_store, calendar):
    store, owner = isolated_evidence_store
    from psycopg.types.json import Jsonb
    from yta_alpha.forecast import snapshot_hash

    snapshot = replay_snapshot(calendar).model_copy(update={"source_id": f"conflict-{uuid4()}"})
    digest = snapshot_hash(snapshot)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) VALUES(%s,%s,%s,%s)",
            (digest, "REPLAY", snapshot.provider, Jsonb(snapshot.model_dump(mode="json"))),
        )
        connection.execute(
            "INSERT INTO yta.evidence_items"
            "(content_hash,source_id,provider,data_mode,as_of,available_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (digest, "wrong-source", snapshot.provider, "REPLAY", snapshot.as_of, snapshot.as_of),
        )
    with pytest.raises(DomainError) as error:
        store.create_draft(owner, snapshot, snapshot.as_of)
    assert error.value.code == "EVIDENCE_CONFLICT"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT id FROM yta.prediction_drafts WHERE owner_id=%s", (owner,)
            ).fetchone()
            is None
        )


def test_snapshot_hash_conflict_does_not_create_index_or_draft(isolated_evidence_store, calendar):
    store, owner = isolated_evidence_store
    from psycopg.types.json import Jsonb
    from yta_alpha.forecast import snapshot_hash

    snapshot = replay_snapshot(calendar).model_copy(
        update={"source_id": f"hash-conflict-{uuid4()}"}
    )
    digest = snapshot_hash(snapshot)
    bad = snapshot.model_dump(mode="json")
    bad["source_id"] = "conflicting-payload"
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) VALUES(%s,%s,%s,%s)",
            (digest, "REPLAY", snapshot.provider, Jsonb(bad)),
        )
    with pytest.raises(DomainError) as error:
        store.create_draft(owner, snapshot, snapshot.as_of)
    assert error.value.code == "EVIDENCE_CONFLICT"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT content_hash FROM yta.evidence_items WHERE content_hash=%s", (digest,)
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT id FROM yta.prediction_drafts WHERE owner_id=%s", (owner,)
            ).fetchone()
            is None
        )


def make_legacy_evidence(store, owner, draft):
    from psycopg.types.json import Jsonb
    from yta_alpha.forecast import snapshot_hash

    value, original = draft
    snapshot = original.model_copy(update={"source_id": f"legacy-{uuid4()}"})
    digest = snapshot_hash(snapshot)
    saved = {**value["forecast"], "data_snapshot_hash": digest}
    identity = uuid4()
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) VALUES(%s,%s,%s,%s)",
            (digest, "REPLAY", snapshot.provider, Jsonb(snapshot.model_dump(mode="json"))),
        )
        connection.execute(
            "INSERT INTO yta.prediction_drafts "
            "(id,owner_id,data_snapshot_hash,model_version,forecast,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (identity, owner, digest, saved["model_version"], Jsonb(saved), saved["expires_at"]),
        )
    return digest, identity, snapshot


@pytest.fixture
def legacy_evidence(store, owner, draft):
    return make_legacy_evidence(store, owner, draft)


def test_legacy_evidence_read_is_read_only_and_explicit_backfill_is_idempotent(
    store, owner, legacy_evidence
):
    from yta_alpha.backfill_evidence import backfill
    from yta_alpha.models import DataMode

    digest, identity, snapshot = legacy_evidence
    assert store.draft_evidence(owner, identity, DataMode.REPLAY).snapshot == snapshot
    assert backfill(store.url, digest, DataMode.REPLAY)["status"] == "missing"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT content_hash FROM yta.evidence_items WHERE content_hash=%s", (digest,)
            ).fetchone()
            is None
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: backfill(store.url, digest, DataMode.REPLAY, apply=True), range(4))
        )
    assert [r["status"] for r in results].count("indexed") == 1
    assert [r["status"] for r in results].count("already_indexed") == 3
    assert backfill(store.url, digest, DataMode.REPLAY)["status"] == "already_indexed"
    assert store.draft_evidence(owner, identity, DataMode.REPLAY).snapshot == snapshot
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.audit_events "
                "WHERE action='EVIDENCE_INDEX_BACKFILLED' AND resource_id=%s",
                (digest,),
            ).fetchone()["n"]
            == 1
        )


def test_existing_conflicting_index_blocks_reads_and_backfill(isolated_evidence_store, calendar):
    store, owner = isolated_evidence_store
    snapshot = replay_snapshot(calendar)
    draft = store.create_draft(owner, snapshot, snapshot.as_of), snapshot
    legacy_evidence = make_legacy_evidence(store, owner, draft)
    from yta_alpha.backfill_evidence import backfill
    from yta_alpha.models import DataMode

    digest, identity, snapshot = legacy_evidence
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO yta.evidence_items "
            "(content_hash,source_id,provider,data_mode,as_of,available_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (digest, "wrong-source", snapshot.provider, "REPLAY", snapshot.as_of, snapshot.as_of),
        )
    with pytest.raises(DomainError) as error:
        store.draft_evidence(owner, identity, DataMode.REPLAY)
    assert error.value.code == "EVIDENCE_INVALID"
    for apply in (False, True):
        with pytest.raises(DomainError) as error:
            backfill(store.url, digest, DataMode.REPLAY, apply=apply)
        assert error.value.code == "EVIDENCE_CONFLICT"


def test_backfill_is_mode_scoped(store, legacy_evidence):
    from yta_alpha.backfill_evidence import backfill
    from yta_alpha.models import DataMode

    digest, _, _ = legacy_evidence
    with pytest.raises(DomainError) as error:
        backfill(store.url, digest, DataMode.LIVE_RESEARCH, apply=True)
    assert error.value.code == "NOT_FOUND"
    assert backfill(store.url, digest, DataMode.REPLAY)["status"] == "missing"


def test_backfill_audit_failure_rolls_back_index(store, legacy_evidence, monkeypatch):
    import yta_alpha.backfill_evidence as module
    from yta_alpha.models import DataMode

    digest, _, _ = legacy_evidence

    def broken_audit(_payload):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module, "Jsonb", broken_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        module.backfill(store.url, digest, DataMode.REPLAY, apply=True)
    assert module.backfill(store.url, digest, DataMode.REPLAY)["status"] == "missing"


def test_worker_stop_during_provider_finishes_current_and_leaves_next_unattempted(
    store, owner, calendar
):
    from yta_alpha.jobs import ReplayProvider, SettlementWorker
    from yta_alpha.models import DataMode

    snapshot = replay_snapshot(calendar)
    predictions = []
    for index in range(2):
        draft = store.create_draft(owner, snapshot, snapshot.as_of)
        predictions.append(
            store.freeze(
                owner, UUID(draft["draft_id"]), f"drain-{index}", confirmed=True, now=snapshot.as_of
            )
        )
    stop, started, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    observed = replay_observation(calendar)

    class Provider:
        def observation(self, forecast, now):
            calls.append(forecast)
            started.set()
            assert release.wait(5)
            return observed

    worker = SettlementWorker(
        store, Provider(), DataMode.REPLAY, dispatch_allowed=lambda: not stop.is_set()
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.tick, observed.as_of, owner=owner)
        try:
            assert started.wait(5)
            stop.set()
        finally:
            release.set()
        results = future.result(timeout=5)
    assert len(calls) == len(results) == 1 and results[0]["status"] == "SETTLED"
    with store._connect() as connection:
        attempts = connection.execute(
            "SELECT a.status FROM yta.settlement_attempts a JOIN yta.predictions p "
            "ON p.id=a.prediction_id WHERE p.owner_id=%s",
            (owner,),
        ).fetchall()
    assert attempts == [{"status": "SETTLED"}]
    assert worker.queue_status(observed.as_of, owner=owner)["unattempted_count"] == 1
    assert worker.tick(observed.as_of, owner=owner) == []
    resumed = SettlementWorker(store, ReplayProvider(calendar), DataMode.REPLAY)
    assert len(resumed.tick(observed.as_of, owner=owner)) == 1
    for prediction in predictions:
        saved = store.get_prediction(owner, UUID(prediction["prediction_id"]))
        assert saved["forecast"] == prediction["forecast"]
        assert len(saved["outcomes"]) == 1


def test_worker_stop_after_lease_prevents_provider_and_attempt(
    store, owner, draft, calendar, monkeypatch
):
    from yta_alpha.jobs import ReplayProvider, SettlementWorker
    from yta_alpha.models import DataMode

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "stop-before-provider", confirmed=True, now=snapshot.as_of
    )
    pid = UUID(frozen["prediction_id"])
    stop = threading.Event()
    original = store.get_prediction

    def get_prediction(*args):
        result = original(*args)
        stop.set()
        return result

    monkeypatch.setattr(store, "get_prediction", get_prediction)

    class Provider:
        def observation(self, *_args):
            pytest.fail("Stop before provider dispatch must not call provider")

    worker = SettlementWorker(
        store, Provider(), DataMode.REPLAY, dispatch_allowed=lambda: not stop.is_set()
    )
    at = replay_observation(calendar).as_of
    assert worker.process(owner, pid, at) is None
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.settlement_attempts WHERE prediction_id=%s", (pid,)
            ).fetchone()["n"]
            == 0
        )
    monkeypatch.setattr(store, "get_prediction", original)
    assert (
        SettlementWorker(store, ReplayProvider(calendar), DataMode.REPLAY).process(owner, pid, at)[
            "status"
        ]
        == "SETTLED"
    )


def test_paused_jobs_cli_reports_queue_and_sigterm_exits_without_attempt(
    store, owner, draft, calendar, tmp_path
):
    import json
    import signal
    import subprocess
    import sys
    import time

    from yta_alpha.worker_health import probe

    value, snapshot = draft
    frozen = store.freeze(
        owner, UUID(value["draft_id"]), "paused-cli", confirmed=True, now=snapshot.as_of
    )
    at = replay_observation(calendar).as_of
    heartbeat = tmp_path / "heartbeat.json"
    command = [
        sys.executable,
        "-m",
        "yta_alpha.jobs",
        "--watch",
        "--interval",
        "60",
        "--owner",
        str(owner),
        "--replay-at",
        at.isoformat(),
        "--heartbeat-file",
        str(heartbeat),
    ]
    env = {
        **os.environ,
        "YTA_ENV": "test",
        "YTA_DATA_MODE": "REPLAY",
        "YTA_ALPHA_TOKEN": "synthetic-worker-token-" + "a" * 32,
        "YTA_DATABASE_URL": store.url,
        "YTA_GIT_SHA": "a" * 40,
        "YTA_JOBS_DISPATCH": "PAUSED",
    }
    process = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 10
        while not heartbeat.exists() or json.loads(heartbeat.read_text())["state"] != "paused":
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        assert probe(heartbeat, "REPLAY", 60)["reason"] == "dispatch_paused"
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
        assert process.returncode == 0
        reports = [json.loads(line) for line in output.splitlines()]
        assert reports and all(
            r["dispatch_status"] == "PAUSED" and r["results"] == [] for r in reports
        )
        assert reports[0]["queue"]["unattempted_count"] == 1
        assert json.loads(heartbeat.read_text())["state"] == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM yta.settlement_attempts WHERE prediction_id=%s",
                (UUID(frozen["prediction_id"]),),
            ).fetchone()["n"]
            == 0
        )
    assert store.get_prediction(owner, UUID(frozen["prediction_id"]))["outcomes"] == []
