"""Test infrastructure: one disposable PostgreSQL container per session,
migrated via the real Alembic path, truncated between tests."""

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parent.parent
PG_IMAGE = "postgres:16-alpine"

ALL_TABLES = (
    "release_code_exceptions, "
    "exploratory_research_reports, exploratory_research, "
    "experiment_outcomes, experiment_records, "
    "monthly_summary_reports, batch_report_input_state, monthly_report_input_state, "
    "artifacts, evaluation_reports, outcome_revisions, forecast_commit_events, "
    "predictions, forecast_commits, ledger_chains, forecast_cases, "
    "forecast_batches, campaigns, campaign_control_events, release_approvals, training_manifests, "
    "research_releases, panel_registrations, "
    "snapshots, calendar_days, calendar_builds, "
    "price_observations, raw_objects, security_identities, securities, "
    "data_sources, events, attempts, jobs, runs, api_keys, tenants"
)


def _docker() -> str:
    for cand in (shutil.which("docker"), "/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if cand and Path(cand).exists():
            return cand
    pytest.skip("docker not available")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_url():
    docker = _docker()
    port = _free_port()
    name = f"youwei-test-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            docker, "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_USER=youwei",
            "-e", "POSTGRES_PASSWORD=youwei",
            "-e", "POSTGRES_DB=youwei",
            "-p", f"{port}:5432",
            PG_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    url = f"postgresql+asyncpg://youwei:youwei@127.0.0.1:{port}/youwei"
    try:
        # The entrypoint temporary server accepts sockets before final TCP startup.
        for _ in range(120):
            r = subprocess.run(
                [docker, "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "youwei"],
                capture_output=True,
            )
            if r.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("postgres container never became ready")

        # Apply the real migrations (not metadata.create_all): the test
        # database proves migrations work on every run.
        # Alembic's asyncio.run resets the calling thread's current loop. Run
        # the real CLI out of process so preceding pure async contract tests
        # cannot lose pytest's session loop when PG starts lazily afterwards.
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
            cwd=REPO_ROOT, env={**os.environ, "YOUWEI_DATABASE_URL": url}, check=True,
            capture_output=True,
        )
        yield url
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True)


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(pg_url):
    """Truncate all tables between tests: one container, fast isolation."""
    from sqlalchemy import text

    from youwei_core.db.engine import make_engine

    engine = make_engine(pg_url, pool_size=1)
    try:
        async with engine.begin() as conn:
            # ledger tables are append-only at the DB level; the ops
            # escape hatch (youwei.ledger_mutation) is only for test
            # cleanup and operator recovery
            await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
            await conn.execute(text(f"TRUNCATE TABLE {ALL_TABLES} RESTART IDENTITY CASCADE"))
        yield
    finally:
        await engine.dispose()


ADMIN_TEST_KEY = "ywa-admin-test-key-do-not-use"


@pytest_asyncio.fixture
async def client(pg_url):
    from youwei_core.api.app import create_app
    from youwei_core.config import Settings

    app = create_app(
        Settings(database_url=pg_url, admin_api_key=ADMIN_TEST_KEY)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


@pytest_asyncio.fixture
def admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN_TEST_KEY}"}


@pytest_asyncio.fixture
async def db_engine(pg_url):
    from youwei_core.db.engine import make_engine

    engine = make_engine(pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def tenant_headers(db_engine, tenant_id) -> dict:
    """Real API key for a fresh tenant (S02b auth)."""
    from youwei_core.auth.service import create_api_key, create_tenant

    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    _, raw = await create_api_key(db_engine, tenant_id, "test")
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def other_tenant_headers(db_engine) -> dict:
    from youwei_core.auth.service import create_api_key, create_tenant

    tenant = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{tenant}", tenant_id=tenant)
    _, raw = await create_api_key(db_engine, tenant, "test-other")
    return {"Authorization": f"Bearer {raw}"}
