import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from yta_alpha.api import create_app
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode
from yta_alpha.research_tools import ResearchTools, ToolObservationRequest
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore

pytestmark = pytest.mark.postgres
MODE = DataMode.REPLAY
SERVICE = "fixture-service-credential-distinct-123456789"
TOOLS = "fixture-read-only-tools-credential-123456789"


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database required")
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test")
    migrate(url, Path(__file__).resolve().parents[2] / "infra/migrations")
    with psycopg.connect(url) as c:
        c.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
    config.update(user="yta_app", password="local-yta-app")
    return url, make_conninfo(**config)


@pytest.fixture
def case(database):
    owner, session, run = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database[0]) as c:
        c.execute("INSERT INTO yta.users(id,email) VALUES(%s,%s)", (owner, f"{owner}@test.invalid"))
        c.execute(
            "INSERT INTO yta.research_sessions(id,owner_id,profile_id,data_mode,"
            "request_key,request_hash) VALUES(%s,%s,'alpha-orchestrator','REPLAY',%s,%s)",
            (session, owner, str(session), "a" * 64),
        )
        c.execute(
            "INSERT INTO yta.research_runs(id,session_id,owner_id,request_id,request_hash,"
            "prompt) VALUES(%s,%s,%s,%s,%s,'research NVDA')",
            (run, session, owner, uuid4(), "b" * 64),
        )
    calendar = TradingCalendar()
    store = ResearchStore(database[1], calendar, "a" * 40)
    return ResearchTools(store), owner, run, replay_snapshot(calendar, count=21)


def test_fixed_snapshot_concurrent_receipts_and_audit(case, database):
    tools, owner, run, snapshot = case
    first = tools.create(owner, run, MODE, lambda: snapshot)
    assert tools.create(owner, run, MODE, lambda: pytest.fail("Snapshot reloaded")) == first
    request = ToolObservationRequest(context_id=run, request_id=uuid4(), tool="market_snapshot")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: tools.observe(request, MODE), range(4)))
    assert all(result == results[0] for result in results)
    assert results[0]["result"]["snapshot"] == snapshot.model_dump(mode="json")
    tools.observe(request.model_copy(update={"request_id": uuid4(), "tool": "indicators"}), MODE)
    receipts = tools.get(owner, run, MODE)["receipts"]
    assert {row["tool"] for row in receipts} == {"market_snapshot", "indicators"}
    assert all("result" not in row for row in receipts)
    with psycopg.connect(database[0]) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM yta.audit_events WHERE resource_id=%s "
                "AND action='research.tool_observed'",
                (str(request.request_id),),
            ).fetchone()[0]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) FROM yta.audit_events WHERE resource_id=%s "
                "AND action='research.context_fixed'",
                (str(run),),
            ).fetchone()[0]
            == 1
        )


def test_owner_mode_and_missing_context_fail_closed(case):
    tools, owner, run, snapshot = case
    request = ToolObservationRequest(context_id=run, request_id=uuid4(), tool="indicators")
    with pytest.raises(DomainError, match="not found"):
        tools.observe(request, MODE)
    tools.create(owner, run, MODE, lambda: snapshot)
    for wrong_owner, mode in [(uuid4(), MODE), (owner, DataMode.LIVE_RESEARCH)]:
        with pytest.raises(DomainError, match="not found"):
            tools.get(wrong_owner, run, mode)
        with pytest.raises(DomainError, match="not found"):
            tools.create(wrong_owner, run, mode, lambda: pytest.fail("Unauthorized load"))
    with pytest.raises(DomainError, match="not found"):
        tools.observe(request, DataMode.LIVE_RESEARCH)


def test_receipt_identity_cannot_change_tool(case):
    tools, owner, run, snapshot = case
    tools.create(owner, run, MODE, lambda: snapshot)
    request = ToolObservationRequest(context_id=run, request_id=uuid4(), tool="indicators")
    tools.observe(request, MODE)
    with pytest.raises(DomainError) as failure:
        tools.observe(request.model_copy(update={"tool": "market_snapshot"}), MODE)
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(tools.get(owner, run, MODE)["receipts"]) == 1


@pytest.mark.parametrize("table", ["research_tool_contexts", "research_tool_observations"])
def test_fact_tables_immutable_for_owner_and_runtime(case, database, table):
    tools, owner, run, snapshot = case
    tools.create(owner, run, MODE, lambda: snapshot)
    tools.observe(
        ToolObservationRequest(context_id=run, request_id=uuid4(), tool="indicators"), MODE
    )
    for url in database:
        for statement in [
            f"UPDATE yta.{table} SET run_id=run_id",
            f"DELETE FROM yta.{table}",
            f"TRUNCATE yta.{table} CASCADE",
        ]:
            with psycopg.connect(url) as c, pytest.raises(psycopg.Error) as error:
                c.execute(statement)
            assert error.value.sqlstate in ("42501", "55000")


def test_http_roles_and_authenticated_context(case, database):
    tools, owner, run, _ = case
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_url=database[1],
                alpha_token=SecretStr(SERVICE),
                alpha_tools_token=SecretStr(TOOLS),
            ),
            calendar=tools.store.calendar,
        )
    )
    service = {"Authorization": f"Bearer {SERVICE}", "X-YTA-Owner-ID": str(owner)}
    tool = {"Authorization": f"Bearer {TOOLS}"}
    path = "/internal/v1/research-tool-contexts"
    assert client.post(path, headers=tool, json={"run_id": str(run)}).status_code == 401
    assert client.post(path, headers=service, json={"run_id": str(run)}).status_code == 200
    body = {"context_id": str(run), "request_id": str(uuid4()), "tool": "indicators"}
    endpoint = "/internal/v1/tools/observations"
    assert client.post(endpoint, headers=service, json=body).status_code == 401
    assert client.post(endpoint, headers=tool, json=body).status_code == 200
    assert client.get(f"{path}/{run}", headers=tool).status_code == 401
    service["X-YTA-Owner-ID"] = str(uuid4())
    assert client.get(f"{path}/{run}", headers=service).status_code == 404
    assert (
        client.post(endpoint, headers=tool, json={**body, "owner_id": str(owner)}).status_code
        == 422
    )


def test_scoped_mcp_reads_fixed_context(case, database):
    import asyncio

    import httpx
    from mcp import Client
    from yta_tools.server import Settings as ToolSettings
    from yta_tools.server import create_server

    tools, owner, run, snapshot = case
    tools.create(owner, run, MODE, lambda: snapshot)
    alpha = create_app(
        Settings(
            _env_file=None,
            database_url=database[1],
            alpha_token=SecretStr(SERVICE),
            alpha_tools_token=SecretStr(TOOLS),
        ),
        calendar=tools.store.calendar,
    )
    settings = ToolSettings(
        alpha_url="http://alpha", alpha_token=SecretStr(TOOLS), data_mode=MODE, scoped_receipts=True
    )

    async def check():
        async with Client(create_server(settings, httpx.ASGITransport(app=alpha))) as client:
            for name in ("market_snapshot", "indicators"):
                result = await client.call_tool(name, {"context_id": str(run)})
                assert not result.is_error, result.content
                body = result.structured_content
                assert body["context_id"] == str(run)
                assert body["tool"] == name
                assert (
                    body["data_snapshot_hash"] == tools.summary(run, snapshot)["data_snapshot_hash"]
                )
            for arguments in (
                {},
                {"context_id": "bad"},
                {"context_id": str(run), "owner_id": str(owner)},
            ):
                result = await client.call_tool("indicators", arguments)
                assert result.is_error
            assert len(tools.get(owner, run, MODE)["receipts"]) == 2

    asyncio.run(check())


def test_receipt_budget_preserves_successful_retries(case):
    tools, owner, run, snapshot = case
    tools.create(owner, run, MODE, lambda: snapshot)
    request = None
    for _ in range(100):
        request = ToolObservationRequest(context_id=run, request_id=uuid4(), tool="market_snapshot")
        tools.observe(request, MODE)
    original = tools.observe(request, MODE)
    with pytest.raises(DomainError) as failure:
        tools.observe(request.model_copy(update={"request_id": uuid4()}), MODE)
    assert failure.value.code == "TOOL_BUDGET_EXCEEDED"
    assert tools.observe(request, MODE) == original
    assert len(tools.get(owner, run, MODE)["receipts"]) == 100


def test_draft_uses_context_snapshot_and_authenticated_run(case, database):
    tools, owner, run, snapshot = case
    tools.create(owner, run, MODE, lambda: snapshot)
    draft = tools.draft(owner, run, MODE, lambda: pytest.fail("REPLAY uses fixed clock"))
    assert (
        draft["forecast"]["data_snapshot_hash"]
        == tools.summary(run, snapshot)["data_snapshot_hash"]
    )
    assert tools.draft(owner, run, MODE, lambda: None) == draft
    with pytest.raises(DomainError, match="not found"):
        tools.draft(uuid4(), run, MODE, lambda: None)
    with pytest.raises(DomainError, match="not found"):
        tools.draft(owner, run, DataMode.LIVE_RESEARCH, lambda: None)
    with psycopg.connect(database[0]) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM yta.predictions WHERE owner_id=%s", (owner,)
            ).fetchone()[0]
            == 0
        )
