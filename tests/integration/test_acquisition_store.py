import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.massive import MassiveCandidate, RawPage
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode

pytestmark = pytest.mark.postgres
START, END = date(2024, 1, 1), date(2025, 1, 1)


@pytest.fixture(scope="module")
def database():
    owner = os.environ.get("YTA_TEST_DATABASE_URL")
    if not owner:
        pytest.skip("Set YTA_TEST_DATABASE_URL for real PostgreSQL integration tests")
    config = conninfo_to_dict(owner)
    assert config["dbname"].endswith("_test")
    migrate(owner, Path(__file__).resolve().parents[2] / "infra/migrations")
    with psycopg.connect(owner) as connection:
        connection.execute(
            "GRANT SELECT, INSERT ON yta.provider_acquisitions, "
            "yta.provider_response_pages, yta.market_bars TO yta_app"
        )
    config.update(user="yta_app", password="local-yta-app")
    runtime = os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL") or make_conninfo(**config)
    return owner, runtime


@pytest.fixture
def acquisition():
    def handler(request):
        payload = {"status": "OK", "results": []}
        if request.url.path.startswith("/v2/aggs/"):
            payload.update(ticker="NVDA", adjusted=False)
        return httpx.Response(200, json=payload)

    return asyncio.run(
        MassiveCandidate(
            SecretStr("synthetic-test-credential"),
            transport=httpx.MockTransport(handler),
            clock=lambda: datetime.now(UTC),
        ).acquire(START, END)
    )


def save(store, acquisition, mode=DataMode.REPLAY):
    return store.save(acquisition, start=START, end=END, data_mode=mode)


def test_concurrent_retries_atomically_save_raw_pages_and_one_audit(database, acquisition):
    owner, runtime = database
    store = AcquisitionStore(runtime)
    with ThreadPoolExecutor(max_workers=6) as executor:
        hashes = list(executor.map(lambda _: save(store, acquisition), range(12)))
    assert len(set(hashes)) == 1
    digest = hashes[0]
    with psycopg.connect(owner) as connection:
        manifest, mode, qualification = connection.execute(
            "SELECT manifest,data_mode,qualification_status "
            "FROM yta.provider_acquisitions WHERE hash=%s",
            (digest,),
        ).fetchone()
        assert mode == "REPLAY" and qualification == "UNVERIFIED"
        assert manifest["requested_start"] == START.isoformat()
        assert manifest["pages"][0]["path"] == acquisition.bars[0].path
        assert manifest["pages"][0]["query"] == [list(pair) for pair in acquisition.bars[0].query]
        pages = connection.execute(
            "SELECT kind,body,content_hash,retrieved_at FROM yta.provider_response_pages "
            "WHERE acquisition_hash=%s ORDER BY kind",
            (digest,),
        ).fetchall()
        assert len(pages) == 2
        for row, page in zip(pages, (acquisition.bars[0], acquisition.splits[0]), strict=True):
            assert (bytes(row[1]), row[2], row[3]) == (
                page.body,
                page.content_hash,
                page.retrieved_at,
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events "
                "WHERE action='provider.acquire' AND resource_id=%s",
                (digest,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.data_snapshots WHERE hash=%s",
                (digest,),
            ).fetchone()[0]
            == 0
        )


def test_later_retrieval_revision_and_mode_are_distinct_evidence(database, acquisition):
    store = AcquisitionStore(database[1])
    first = save(store, acquisition)
    later = replace(
        acquisition,
        bars=tuple(
            replace(page, retrieved_at=page.retrieved_at + timedelta(seconds=1))
            for page in acquisition.bars
        ),
    )
    revised = replace(
        acquisition, bars=tuple(replace(page, body=page.body + b" ") for page in acquisition.bars)
    )
    assert (
        len(
            {
                first,
                save(store, later),
                save(store, revised),
                save(store, acquisition, DataMode.DEMO),
            }
        )
        == 4
    )
    assert save(store, acquisition) == first


@pytest.mark.parametrize("table", ["provider_acquisitions", "provider_response_pages"])
def test_provider_evidence_is_immutable_even_for_owner(database, acquisition, table):
    save(AcquisitionStore(database[1]), acquisition)
    for command in [
        f"UPDATE yta.{table} SET created_at=created_at"
        if table == "provider_acquisitions"
        else f"UPDATE yta.{table} SET body=body",
        f"DELETE FROM yta.{table}",
        f"TRUNCATE yta.{table} CASCADE",
    ]:
        with (
            psycopg.connect(database[0]) as connection,
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        ):
            connection.execute(command)
        with (
            psycopg.connect(database[1]) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(command)


def test_database_hash_constraint_rolls_back_manifest_and_audit(database, acquisition):
    class CorruptPage(RawPage):
        @property
        def content_hash(self):
            return "0" * 64

    page = acquisition.bars[0]
    corrupt = replace(
        acquisition, bars=(CorruptPage(page.path, page.query, page.retrieved_at, page.body),)
    )

    def counts():
        with psycopg.connect(database[0]) as connection:
            return tuple(
                connection.execute(f"SELECT count(*) FROM yta.{table}").fetchone()[0]
                for table in ("provider_acquisitions", "provider_response_pages", "audit_events")
            )

    before = counts()
    with pytest.raises(psycopg.errors.CheckViolation):
        save(AcquisitionStore(database[1]), corrupt)
    assert counts() == before


@pytest.mark.parametrize("change", ["empty", "credential_query", "wrong_path", "naive_time"])
def test_invalid_evidence_rejected_before_opening_database(acquisition, change):
    page = acquisition.bars[0]
    if change == "empty":
        acquisition = replace(acquisition, bars=())
    else:
        if change == "credential_query":
            page = replace(page, query=(("apiKey", "must-not-be-stored"),))
        elif change == "wrong_path":
            page = replace(page, path="/other")
        else:
            page = replace(page, retrieved_at=page.retrieved_at.replace(tzinfo=None))
        acquisition = replace(acquisition, bars=(page,))
    with pytest.raises(ValueError):
        save(AcquisitionStore("invalid-connection-string"), acquisition)


def test_operator_entrypoint_collects_to_real_database(database, monkeypatch, capsys):
    import json

    from yta_alpha import acquire

    calls = []

    def handler(request):
        calls.append(request)
        payload = {"status": "OK", "results": []}
        if request.url.path.startswith("/v2/aggs/"):
            payload.update(ticker="NVDA", adjusted=False)
        return httpx.Response(200, json=payload)

    monkeypatch.setenv("YTA_ACQUIRE_MASSIVE_TOKEN", "synthetic-test-credential")
    monkeypatch.setenv("YTA_ACQUIRE_DATABASE_URL", database[1])
    monkeypatch.setattr(
        acquire,
        "MassiveCandidate",
        lambda token: MassiveCandidate(token, transport=httpx.MockTransport(handler)),
    )
    assert (
        acquire.main(
            ["--start", START.isoformat(), "--end", END.isoformat(), "--data-mode", "REPLAY"]
        )
        == 0
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not output.err and len(calls) == 2
    assert result["qualification_status"] == "UNVERIFIED"
    assert result["bar_count"] == result["split_count"] == 0
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.provider_response_pages WHERE acquisition_hash=%s",
                (result["acquisition_hash"],),
            ).fetchone()[0]
            == 2
        )


def test_owner_credentials_rejected_before_provider_call(database, monkeypatch):
    from yta_alpha import acquire
    from yta_alpha.errors import DomainError

    def forbidden(*args):
        pytest.fail("Owner credentials must not enable provider calls")

    monkeypatch.setattr(acquire, "MassiveCandidate", forbidden)
    settings = acquire.AcquisitionSettings(
        massive_token=SecretStr("synthetic-test-credential"), database_url=SecretStr(database[0])
    )
    with pytest.raises(DomainError) as error:
        asyncio.run(acquire.collect(settings, START, END, DataMode.REPLAY))
    assert error.value.code == "ACQUISITION_STORAGE_NOT_READY"


def test_saved_evidence_roundtrip_and_mode_scoping(database, acquisition):
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    digest = save(store, acquisition)
    loaded = store.load(digest, data_mode=DataMode.REPLAY)
    assert (loaded.start, loaded.end, loaded.data_mode) == (START, END, DataMode.REPLAY)
    assert loaded.acquisition == acquisition
    for unknown, mode in [(digest, DataMode.LIVE_RESEARCH), ("f" * 64, DataMode.REPLAY)]:
        with pytest.raises(DomainError) as error:
            store.load(unknown, data_mode=mode)
        assert error.value.code == "NOT_FOUND"


@pytest.mark.parametrize("corruption", ["missing_page", "metadata", "manifest_hash", "page_index"])
def test_inconsistent_inserted_evidence_is_detected_without_modifying_facts(
    database, acquisition, corruption
):
    from psycopg.types.json import Jsonb
    from yta_alpha.errors import DomainError
    from yta_alpha.forecast import content_hash

    store = AcquisitionStore(database[1])
    manifest = store._manifest(acquisition, START, END, DataMode.REPLAY)
    if corruption == "metadata":
        manifest["pages"][0]["retrieved_at"] = "2000-01-01T00:00:00+00:00"
    elif corruption == "page_index":
        manifest["pages"][0]["page_index"] = 1
    digest = content_hash(manifest)
    if corruption == "manifest_hash":
        manifest["version"] = "tampered"
    # Deliberately insert inconsistent evidence through direct SQL, without weakening
    # immutable triggers or modifying earlier records, to test the reader's boundary.
    with psycopg.connect(database[1]) as connection:
        connection.execute(
            "INSERT INTO yta.provider_acquisitions "
            "(hash,provider,symbol,data_mode,requested_start,requested_end,"
            "qualification_status,manifest) "
            "VALUES (%s,'massive','NVDA','REPLAY',%s,%s,'UNVERIFIED',%s)",
            (digest, START, END, Jsonb(manifest)),
        )
        for kind, page in [("bars", acquisition.bars[0]), ("splits", acquisition.splits[0])]:
            if corruption == "missing_page" and kind == "splits":
                continue
            index = 1 if corruption == "page_index" and kind == "bars" else 0
            connection.execute(
                "INSERT INTO yta.provider_response_pages "
                "(acquisition_hash,kind,page_index,content_hash,retrieved_at,body) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (digest, kind, index, page.content_hash, page.retrieved_at, page.body),
            )
    with pytest.raises(DomainError) as error:
        store.load(digest, data_mode=DataMode.REPLAY)
    assert error.value.code == "ACQUISITION_INTEGRITY_FAILED"


def test_readonly_inspection_command_needs_no_provider_credential(
    database, acquisition, monkeypatch
):
    import json
    import subprocess
    import sys

    digest = save(AcquisitionStore(database[1]), acquisition)
    env = {key: value for key, value in os.environ.items() if not key.startswith("YTA_ACQUIRE_")}
    env["YTA_ACQUIRE_DATABASE_URL"] = database[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yta_alpha.inspect_acquisition",
            "--hash",
            digest,
            "--data-mode",
            "REPLAY",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and not result.stderr
    report = json.loads(result.stdout)
    assert report["integrity_checked"] is True and report["qualification_status"] == "UNVERIFIED"
    assert report["bar_pages"] == report["split_pages"] == 1
    assert report["requested_start"] == START.isoformat()
    assert report["quality"]["status"] == "ISSUES_FOUND"
    assert report["quality"]["missing_sessions"]
    assert report["quality"]["qualification_status"] == "UNVERIFIED"


def test_load_reconstructs_precise_split_event_from_original_bytes(database, acquisition):
    from decimal import Decimal

    from yta_alpha.massive import normalize_splits

    raw = (
        b'{"status":"OK","results":[{"id":"fixture-exact","ticker":"NVDA",'
        b'"execution_date":"2024-06-10","split_from":1.000000000000000001,'
        b'"split_to":1.000000000000000002,"adjustment_type":"forward_split"}]}'
    )
    page = replace(acquisition.splits[0], body=raw)
    acquisition = replace(
        acquisition, splits=(page,), split_events=normalize_splits([page], START, END)
    )
    store = AcquisitionStore(database[1])
    loaded = store.load(save(store, acquisition), data_mode=DataMode.REPLAY).acquisition
    assert loaded == acquisition and loaded.splits[0].body == raw
    assert loaded.split_events[0].split_to == Decimal("1.000000000000000002")


def test_inspection_compares_saved_versions_without_writing(
    database, acquisition, monkeypatch, capsys
):
    import json

    from yta_alpha.inspect_acquisition import main

    store = AcquisitionStore(database[1])
    original = save(store, acquisition)
    revised = replace(
        acquisition, bars=(replace(acquisition.bars[0], body=acquisition.bars[0].body + b" "),)
    )
    current = save(store, revised)
    monkeypatch.setenv("YTA_ACQUIRE_DATABASE_URL", database[1])
    with psycopg.connect(database[0]) as connection:
        audit_before = connection.execute("SELECT count(*) FROM yta.audit_events").fetchone()[0]
    assert main(["--hash", current, "--compare-hash", original, "--data-mode", "REPLAY"]) == 0
    output = capsys.readouterr()
    report = json.loads(output.out)["comparison"]
    assert not output.err
    assert (report["before_hash"], report["after_hash"]) == (original, current)
    assert report["status"] == "NO_DATA_CHANGE" and report["raw_pages_changed"] is True
    assert store.load(original, data_mode=DataMode.REPLAY).acquisition == acquisition
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute("SELECT count(*) FROM yta.audit_events").fetchone()[0]
            == audit_before
        )


def test_lock_timeout_writes_nothing_and_same_evidence_retry_succeeds(
    database, acquisition, monkeypatch
):
    import yta_alpha.acquisition_store as module
    from yta_alpha.forecast import content_hash

    monkeypatch.setattr(module, "LOCK_TIMEOUT_MS", 50)
    store = AcquisitionStore(database[1])
    digest = content_hash(store._manifest(acquisition, START, END, DataMode.REPLAY))
    with psycopg.connect(database[0]) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"acquisition:{digest}",)
        )
        with pytest.raises(psycopg.errors.LockNotAvailable):
            save(store, acquisition)
        assert (
            blocker.execute(
                "SELECT count(*) FROM yta.provider_acquisitions WHERE hash=%s", (digest,)
            ).fetchone()[0]
            == 0
        )
        assert (
            blocker.execute(
                "SELECT count(*) FROM yta.audit_events WHERE resource_id=%s", (digest,)
            ).fetchone()[0]
            == 0
        )
    assert save(store, acquisition) == save(store, acquisition) == digest
    assert store.load(digest, data_mode=DataMode.REPLAY).acquisition == acquisition
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events WHERE resource_id=%s", (digest,)
            ).fetchone()[0]
            == 1
        )


def test_statement_deadline_cancels_query_and_connection_transaction_rolls_back(
    database, monkeypatch
):
    import yta_alpha.acquisition_store as module

    monkeypatch.setattr(module, "STATEMENT_TIMEOUT_MS", 50)
    with module.acquisition_connection(database[1]) as connection:
        with pytest.raises(psycopg.errors.QueryCanceled):
            with connection.transaction():
                connection.execute("SELECT pg_sleep(1)")
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_read_waits_are_bounded_while_schema_is_locked(database, acquisition, monkeypatch):
    import yta_alpha.acquisition_store as module

    store = AcquisitionStore(database[1])
    digest = save(store, acquisition)
    monkeypatch.setattr(module, "LOCK_TIMEOUT_MS", 50)
    with psycopg.connect(database[0]) as blocker:
        blocker.execute("LOCK TABLE yta.provider_acquisitions IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            store.load(digest, data_mode=DataMode.REPLAY)
    assert store.load(digest, data_mode=DataMode.REPLAY).acquisition == acquisition


@pytest.mark.parametrize("unexpected", [False, True])
def test_failed_provider_attempt_is_audited_without_response_or_credential(
    database, monkeypatch, unexpected, capsys
):
    import json

    from yta_alpha import acquire

    secret = "upstream-sensitive-text"

    def factory(token):
        if unexpected:
            raise RuntimeError(secret)
        return MassiveCandidate(
            token, transport=httpx.MockTransport(lambda request: httpx.Response(429, text=secret))
        )

    monkeypatch.setattr(acquire, "MassiveCandidate", factory)
    monkeypatch.setenv("YTA_ACQUIRE_MASSIVE_TOKEN", secret)
    monkeypatch.setenv("YTA_ACQUIRE_DATABASE_URL", database[1])
    assert (
        acquire.main(
            ["--start", START.isoformat(), "--end", END.isoformat(), "--data-mode", "REPLAY"]
        )
        == 1
    )
    output = capsys.readouterr()
    failure = json.loads(output.err)
    code = "ACQUISITION_FAILED" if unexpected else "PROVIDER_RATE_LIMITED"
    assert not output.out and failure["error_code"] == code and secret not in output.err
    with psycopg.connect(database[0]) as connection:
        events = connection.execute(
            "SELECT action,payload FROM yta.audit_events WHERE resource_id=%s ORDER BY id",
            (failure["attempt_id"],),
        ).fetchall()
    assert [row[0] for row in events] == ["provider.acquire_started", "provider.acquire_failed"]
    assert events[-1][1] == {"error_code": code}
    assert events[0][1]["requested_start"] == START.isoformat()
    assert secret not in json.dumps(events)


def test_attempt_success_is_atomic_and_terminal_retry_is_idempotent(database, acquisition):
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    args = dict(start=START, end=END, data_mode=DataMode.REPLAY, attempt_id=attempt)
    digest = store.save(acquisition, **args)
    assert store.save(acquisition, **args) == digest
    with pytest.raises(DomainError) as error:
        store.fail_attempt(attempt, "PROVIDER_UNAVAILABLE")
    assert error.value.code == "ACQUISITION_ATTEMPT_FINISHED"
    with psycopg.connect(database[0]) as connection:
        events = connection.execute(
            "SELECT action,payload FROM yta.audit_events WHERE resource_id=%s ORDER BY id",
            (str(attempt),),
        ).fetchall()
    assert [event[0] for event in events] == [
        "provider.acquire_started",
        "provider.acquire_completed",
    ]
    assert events[-1][1] == {"acquisition_hash": digest}


def test_failed_write_does_not_commit_success_event(database, acquisition):
    class CorruptPage(RawPage):
        @property
        def content_hash(self):
            return "0" * 64

    page = acquisition.bars[0]
    bad = replace(
        acquisition, bars=(CorruptPage(page.path, page.query, page.retrieved_at, page.body),)
    )
    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    with pytest.raises(psycopg.errors.CheckViolation):
        store.save(bad, start=START, end=END, data_mode=DataMode.REPLAY, attempt_id=attempt)
    with psycopg.connect(database[0]) as connection:
        actions = connection.execute(
            "SELECT action FROM yta.audit_events WHERE resource_id=%s", (str(attempt),)
        ).fetchall()
    assert actions == [("provider.acquire_started",)]


def test_attempt_scope_mismatch_cannot_complete(database, acquisition):
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.DEMO)
    with pytest.raises(DomainError) as error:
        store.save(acquisition, start=START, end=END, data_mode=DataMode.REPLAY, attempt_id=attempt)
    assert error.value.code == "ACQUISITION_ATTEMPT_MISMATCH"
    store.fail_attempt(attempt, "sensitive-unrecognized-error")
    store.fail_attempt(attempt, "sensitive-unrecognized-error")
    with psycopg.connect(database[0]) as connection:
        failures = connection.execute(
            "SELECT payload FROM yta.audit_events WHERE resource_id=%s "
            "AND action='provider.acquire_failed'",
            (str(attempt),),
        ).fetchall()
    assert failures == [({"error_code": "ACQUISITION_FAILED"},)]


def test_racing_attempt_completion_and_failure_commit_only_one_terminal(database, acquisition):
    from yta_alpha.errors import DomainError
    from yta_alpha.forecast import content_hash

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    digest = content_hash(store._manifest(acquisition, START, END, DataMode.REPLAY))

    def finish(success):
        try:
            if success:
                return store.save(
                    acquisition, start=START, end=END, data_mode=DataMode.REPLAY, attempt_id=attempt
                )
            store.fail_attempt(attempt, "PROVIDER_UNAVAILABLE")
            return "failed"
        except DomainError as error:
            assert error.code == "ACQUISITION_ATTEMPT_FINISHED"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(finish, [True, False]))
    assert results.count("conflict") == 1
    with psycopg.connect(database[0]) as connection:
        terminal = connection.execute(
            "SELECT action FROM yta.audit_events WHERE resource_id=%s "
            "AND action != 'provider.acquire_started'",
            (str(attempt),),
        ).fetchall()
        saved = connection.execute(
            "SELECT count(*) FROM yta.provider_acquisitions WHERE hash=%s", (digest,)
        ).fetchone()[0]
    assert len(terminal) == 1
    assert saved == int(terminal[0][0] == "provider.acquire_completed")


@pytest.mark.parametrize("terminal", ["UNRESOLVED", "FAILED", "COMPLETED"])
def test_attempt_lookup_distinguishes_unresolved_failed_and_verified_completed(
    database, acquisition, terminal
):
    from uuid import uuid4

    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    if terminal == "FAILED":
        store.fail_attempt(attempt, "PROVIDER_RATE_LIMITED")
    elif terminal == "COMPLETED":
        digest = store.save(
            acquisition, start=START, end=END, data_mode=DataMode.REPLAY, attempt_id=attempt
        )
    result = store.get_attempt(attempt, data_mode=DataMode.REPLAY)
    assert result["status"] == terminal and result["qualification_status"] == "UNVERIFIED"
    assert result["requested_start"] == START.isoformat()
    assert (result["finished_at"] is None) == (terminal == "UNRESOLVED")
    if terminal == "COMPLETED":
        assert result["acquisition_hash"] == digest and result["integrity_checked"] is True
    elif terminal == "FAILED":
        assert result["error_code"] == "PROVIDER_RATE_LIMITED"
    else:
        assert "error_code" not in result and "acquisition_hash" not in result
    for identity, mode in [(attempt, DataMode.LIVE_RESEARCH), (uuid4(), DataMode.REPLAY)]:
        with pytest.raises(DomainError) as error:
            store.get_attempt(identity, data_mode=mode)
        assert error.value.code == "NOT_FOUND"


@pytest.mark.parametrize("corruption", ["duplicate_start", "missing_evidence", "unknown_failure"])
def test_attempt_lookup_rejects_inconsistent_audit_facts(database, corruption):
    from psycopg.types.json import Jsonb
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    action, payload = {
        "duplicate_start": ("provider.acquire_started", {}),
        "missing_evidence": ("provider.acquire_completed", {"acquisition_hash": "0" * 64}),
        "unknown_failure": ("provider.acquire_failed", {"error_code": "sensitive-fixture"}),
    }[corruption]
    with psycopg.connect(database[1]) as connection:
        connection.execute(
            "INSERT INTO yta.audit_events(action,resource_id,payload) VALUES (%s,%s,%s)",
            (action, str(attempt), Jsonb(payload)),
        )
    with pytest.raises(DomainError) as error:
        store.get_attempt(attempt, data_mode=DataMode.REPLAY)
    assert error.value.code == "ACQUISITION_ATTEMPT_INTEGRITY_FAILED"
    assert "sensitive-fixture" not in str(error.value)


def test_attempt_inspection_cli_uses_only_database_and_does_not_finalize_pending(
    database, monkeypatch, capsys
):
    import json

    from yta_alpha.inspect_acquisition import main

    store = AcquisitionStore(database[1])
    attempt = store.start_attempt(START, END, DataMode.REPLAY)
    monkeypatch.delenv("YTA_ACQUIRE_MASSIVE_TOKEN", raising=False)
    monkeypatch.setenv("YTA_ACQUIRE_DATABASE_URL", database[1])
    assert main(["--attempt-id", str(attempt), "--data-mode", "REPLAY"]) == 0
    output = capsys.readouterr()
    assert not output.err and json.loads(output.out)["status"] == "UNRESOLVED"
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events WHERE resource_id=%s", (str(attempt),)
            ).fetchone()[0]
            == 1
        )


@pytest.fixture
def daily_acquisition(acquisition):
    # Exact non-float JSON prices and deliberately fractional source volume.
    body = (
        b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":['
        b'{"t":1704171600000,"o":100.12345678901234567890123456789,'
        b'"h":102,"l":99,"c":101,"v":123.456}]}'
    )
    return replace(acquisition, bars=(replace(acquisition.bars[0], body=body),))


def test_daily_projection_precision_concurrency_revision_and_scope(database, daily_acquisition):
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    with ThreadPoolExecutor(max_workers=4) as executor:
        hashes = list(executor.map(lambda _: save(store, daily_acquisition), range(8)))
    assert len(set(hashes)) == 1
    digest = hashes[0]
    rows = store.daily_bars(digest, data_mode=DataMode.REPLAY)
    assert len(rows) == 1
    row = rows[0]
    assert row["open"] == "100.12345678901234567890123456789"
    assert row["volume"] == "123.456"
    assert row["session_date"] == "2024-01-02"
    assert row["available_at"] == daily_acquisition.bars[0].retrieved_at.isoformat()
    assert row["published_at"] is None and row["qualification_status"] == "UNVERIFIED"
    assert row["source_page_hash"] == daily_acquisition.bars[0].content_hash
    with pytest.raises(DomainError, match="Acquisition not found"):
        store.daily_bars(digest, data_mode=DataMode.LIVE_RESEARCH)
    page = daily_acquisition.bars[0]
    revised = replace(
        daily_acquisition, bars=(replace(page, body=page.body.replace(b'"c":101', b'"c":102')),)
    )
    revision = save(store, revised)
    assert revision != digest
    assert store.daily_bars(revision, data_mode=DataMode.REPLAY)[0]["close"] == "102"
    assert store.daily_bars(digest, data_mode=DataMode.REPLAY) == rows
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.market_bars WHERE acquisition_hash=%s", (digest,)
            ).fetchone()[0]
            == 1
        )
    for command in (
        "UPDATE yta.market_bars SET close=close",
        "DELETE FROM yta.market_bars",
        "TRUNCATE yta.market_bars",
    ):
        for url, error in (
            (database[0], psycopg.errors.ObjectNotInPrerequisiteState),
            (database[1], psycopg.errors.InsufficientPrivilege),
        ):
            with psycopg.connect(url) as connection, pytest.raises(error):
                connection.execute(command)


def test_projection_failure_rolls_back_entire_acquisition(database, daily_acquisition, monkeypatch):
    from yta_alpha import acquisition_store
    from yta_alpha.forecast import content_hash

    store = AcquisitionStore(database[1])
    digest = content_hash(store._manifest(daily_acquisition, START, END, DataMode.REPLAY))
    original = acquisition_store.persist_bars

    def fail_after_inserting(connection, digest, rows):
        original(connection, digest, rows)
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(acquisition_store, "persist_bars", fail_after_inserting)
    with pytest.raises(RuntimeError, match="synthetic projection failure"):
        save(store, daily_acquisition)
    with psycopg.connect(database[0]) as connection:
        for table, column in (
            ("provider_acquisitions", "hash"),
            ("provider_response_pages", "acquisition_hash"),
            ("market_bars", "acquisition_hash"),
        ):
            assert (
                connection.execute(
                    f"SELECT count(*) FROM yta.{table} WHERE {column}=%s", (digest,)
                ).fetchone()[0]
                == 0
            )


def test_original_retry_materializes_legacy_evidence(database, daily_acquisition, monkeypatch):
    from yta_alpha import acquisition_store
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    original = acquisition_store.persist_bars
    monkeypatch.setattr(acquisition_store, "persist_bars", lambda *args: None)
    digest = save(store, daily_acquisition)
    with pytest.raises(DomainError, match="Daily records disagree"):
        store.daily_bars(digest, data_mode=DataMode.REPLAY)
    monkeypatch.setattr(acquisition_store, "persist_bars", original)
    assert save(store, daily_acquisition) == digest
    assert len(store.daily_bars(digest, data_mode=DataMode.REPLAY)) == 1
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events "
                "WHERE action='provider.acquire' AND resource_id=%s",
                (digest,),
            ).fetchone()[0]
            == 1
        )


def test_conflicting_projection_is_detected_and_not_overwritten(
    database, daily_acquisition, monkeypatch
):
    from decimal import Decimal

    from yta_alpha import acquisition_store
    from yta_alpha.errors import DomainError

    store = AcquisitionStore(database[1])
    original = acquisition_store.persist_bars

    def wrong_projection(connection, digest, rows):
        altered = [(*rows[0][:7], Decimal("100"), rows[0][8])]
        original(connection, digest, altered)

    monkeypatch.setattr(acquisition_store, "persist_bars", wrong_projection)
    digest = save(store, daily_acquisition)
    monkeypatch.setattr(acquisition_store, "persist_bars", original)
    with pytest.raises(DomainError, match="Daily records disagree"):
        store.daily_bars(digest, data_mode=DataMode.REPLAY)
    with pytest.raises(DomainError, match="Daily records disagree"):
        save(store, daily_acquisition)
    with psycopg.connect(database[0]) as connection:
        assert (
            connection.execute(
                "SELECT close FROM yta.market_bars WHERE acquisition_hash=%s", (digest,)
            ).fetchone()[0]
            == 100
        )


def test_multiple_pages_preserve_gaps_and_source_times(database, daily_acquisition):
    page = daily_acquisition.bars[0]
    later = replace(
        page,
        retrieved_at=page.retrieved_at + timedelta(seconds=10),
        body=page.body.replace(b"1704171600000", b"1704430800000"),
    )
    acquisition = replace(daily_acquisition, bars=(page, later))
    store = AcquisitionStore(database[1])
    digest = save(store, acquisition)
    rows = store.daily_bars(digest, data_mode=DataMode.REPLAY)
    assert [row["session_date"] for row in rows] == ["2024-01-02", "2024-01-05"]
    assert [row["page_index"] for row in rows] == [0, 1]
    assert [row["available_at"] for row in rows] == [
        page.retrieved_at.isoformat(),
        later.retrieved_at.isoformat(),
    ]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "0", "-1"])
def test_market_bar_price_constraints(database, daily_acquisition, value):
    from decimal import Decimal

    from yta_alpha.market_bars import COLUMNS, projected_rows

    digest = save(AcquisitionStore(database[1]), daily_acquisition)
    row = list(projected_rows(daily_acquisition)[0])
    row[1], row[2], row[4] = 1, date(2024, 1, 3), Decimal(value)
    with psycopg.connect(database[1]) as connection, pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            f"INSERT INTO yta.market_bars(acquisition_hash,{COLUMNS}) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (digest, *row),
        )


def test_parquet_partitions_match_database_and_preserve_precision(
    database, daily_acquisition, tmp_path
):
    from yta_alpha.market_lake import export, query

    page = daily_acquisition.bars[0]
    # More than DuckDB DECIMAL's 38 digits: storage must not round or convert to float.
    page = replace(
        page,
        body=page.body.replace(
            b"100.12345678901234567890123456789",
            b"100.12345678901234567890123456789012345678901234567890",
        ),
    )
    later = replace(page, body=page.body.replace(b"1704171600000", b"1735707600000"))
    gap = replace(page, body=page.body.replace(b"1704171600000", b"1704430800000"))
    acquisition = replace(daily_acquisition, bars=(page, gap, later))
    store = AcquisitionStore(database[1])
    digest = save(store, acquisition)
    expected = store.daily_bars(digest, data_mode=DataMode.REPLAY)
    manifest = export(store, tmp_path, digest, DataMode.REPLAY)
    assert manifest.rows == 3 and len(manifest.partitions) == 2
    assert manifest.qualification_status == "UNVERIFIED"
    for year in (2024, 2025):
        actual = query(tmp_path, digest, DataMode.REPLAY, symbol="NVDA", year=year)
        assert actual == [row for row in expected if row["session_date"].startswith(str(year))]
        assert actual[0]["open"] == "100.12345678901234567890123456789012345678901234567890"
        assert actual[0]["published_at"] is None
    assert query(tmp_path, digest, DataMode.REPLAY, symbol="NVDA", year=2023) == []
    assert export(store, tmp_path, digest, DataMode.REPLAY) == manifest


def test_parquet_concurrent_exports_publish_once(database, daily_acquisition, tmp_path):
    from yta_alpha.market_lake import export, load

    store = AcquisitionStore(database[1])
    digest = save(store, daily_acquisition)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: export(store, tmp_path, digest, DataMode.REPLAY), range(4))
        )
    assert all(result == results[0] for result in results)
    assert load(tmp_path, digest, DataMode.REPLAY)[1] == store.daily_bars(
        digest, data_mode=DataMode.REPLAY
    )
    assert not list(tmp_path.rglob(".pending-*"))


@pytest.mark.parametrize("fault", ["file", "manifest", "mode", "traversal", "symlink"])
def test_parquet_tampering_or_wrong_mode_is_rejected(database, daily_acquisition, tmp_path, fault):
    import json

    from yta_alpha.errors import DomainError
    from yta_alpha.market_lake import export, load

    store = AcquisitionStore(database[1])
    digest = save(store, daily_acquisition)
    manifest = export(store, tmp_path, digest, DataMode.REPLAY)
    directory = tmp_path / "REPLAY" / digest
    part = directory / manifest.partitions[0].path
    if fault == "file":
        part.write_bytes(b"damaged")
    elif fault == "manifest":
        value = manifest.model_dump(mode="json")
        value["rows"] += 1
        (directory / "manifest.json").write_text(json.dumps(value))
    elif fault == "traversal":
        value = manifest.model_dump(mode="json")
        value["partitions"][0]["path"] = "../../private.parquet"
        (directory / "manifest.json").write_text(json.dumps(value))
    elif fault == "symlink":
        other = tmp_path / "outside.parquet"
        part.rename(other)
        part.symlink_to(other)
    with pytest.raises(DomainError) as error:
        load(tmp_path, digest, DataMode.LIVE_RESEARCH if fault == "mode" else DataMode.REPLAY)
    assert error.value.code == "MARKET_LAKE_INVALID"
    if fault != "mode":
        with pytest.raises(DomainError):
            export(store, tmp_path, digest, DataMode.REPLAY)


def test_parquet_write_failure_never_publishes_manifest(
    database, daily_acquisition, tmp_path, monkeypatch
):
    import yta_alpha.market_lake as lake

    store = AcquisitionStore(database[1])
    digest = save(store, daily_acquisition)

    def fail(_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(lake, "sync", fail)
    with pytest.raises(OSError):
        lake.export(store, tmp_path, digest, DataMode.REPLAY)
    assert not (tmp_path / "REPLAY" / digest).exists()
    assert not list(tmp_path.rglob(".pending-*"))


def test_empty_parquet_archive_is_explicit_and_query_needs_no_database(
    database, acquisition, tmp_path, monkeypatch, capsys
):
    from yta_alpha.lake_cli import main
    from yta_alpha.market_lake import export

    store = AcquisitionStore(database[1])
    digest = save(store, acquisition)
    manifest = export(store, tmp_path, digest, DataMode.REPLAY)
    assert manifest.rows == 0 and manifest.partitions == []
    monkeypatch.delenv("YTA_ACQUIRE_DATABASE_URL", raising=False)
    assert (
        main(
            [
                "query",
                "--root",
                str(tmp_path),
                "--hash",
                digest,
                "--data-mode",
                "REPLAY",
                "--symbol",
                "NVDA",
                "--year",
                "2024",
            ]
        )
        == 0
    )
    assert '"rows": []' in capsys.readouterr().out


def test_parquet_revisions_and_modes_never_overwrite_original(
    database, daily_acquisition, tmp_path
):
    from yta_alpha.market_lake import export, query

    store = AcquisitionStore(database[1])
    original = save(store, daily_acquisition)
    revised = replace(
        daily_acquisition,
        bars=(
            replace(
                daily_acquisition.bars[0],
                body=daily_acquisition.bars[0].body.replace(b'"c":101', b'"c":102'),
            ),
        ),
    )
    revision = save(store, revised)
    live = save(store, daily_acquisition, mode=DataMode.LIVE_RESEARCH)
    assert len({original, revision, live}) == 3
    for digest, mode in (
        (original, DataMode.REPLAY),
        (revision, DataMode.REPLAY),
        (live, DataMode.LIVE_RESEARCH),
    ):
        result = export(store, tmp_path, digest, mode)
        assert result.qualification_status == "UNVERIFIED"
    assert query(tmp_path, original, DataMode.REPLAY, symbol="NVDA", year=2024)[0]["close"] == "101"
    assert query(tmp_path, revision, DataMode.REPLAY, symbol="NVDA", year=2024)[0]["close"] == "102"
    assert (
        query(tmp_path, live, DataMode.LIVE_RESEARCH, symbol="NVDA", year=2024)[0]["data_mode"]
        == "LIVE_RESEARCH"
    )
