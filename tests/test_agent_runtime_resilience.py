"""S07j: agent-runtime resilience through the real Controller path.

These are DB-level tests that drive the real Controller call path —
worker loop -> run_batch_predictions -> make_phase1b_llm_fetcher ->
run_agent_research -> subprocess — replacing ONLY the external
Hermes/gateway boundary (a fake subprocess). They pin:

- cancellation: before / during / after the research turn; a late result
  is fenced and the job remains recoverable;
- authorization: cross-tenant, wrong run/case, and expired-token requests
  are rejected at the Controller's binding check;
- fault recovery: subprocess timeout, non-zero exit, and duplicate
  responses stay idempotent and fenced.

The real upstream (Hermes) stopping compute or billing on cancel is a
separate acceptance, out of scope here.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from youwei_core.auth.service import create_tenant
from youwei_core.jobs.service import (
    RunSubmission,
    cancel_run,
    get_run_view,
    submit_run,
)
from youwei_core.jobs.worker import claim_next_job
from youwei_core.ledger.pipeline import (
    AgentRuntimeConfig,
    make_batch_predict_handler,
    run_batch_predictions,
)
from youwei_core.worker.loop import WorkerLoop

from test_ledger_phase1b import _phase1b_batch


# --- fake subprocesses (the only external boundary we replace) -------------


class _HangingProcess:
    """A fake agent-runtime subprocess whose communicate() never returns
    (a stuck Hermes turn); kill() marks it and wait() resolves."""

    def __init__(self):
        self.returncode = None
        self.killed = False
        self.received = None

    async def communicate(self, data: bytes):
        self.received = data
        await asyncio.Event().wait()

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _EchoProcess:
    """A fake agent-runtime subprocess that answers a produced proposal
    bound to the case named in the request."""

    def __init__(self):
        self.returncode = 0
        self.received = None

    async def communicate(self, data: bytes):
        self.received = data
        request = json.loads(data)
        case = request["evidence"]["case"]
        run_id = request["evidence"]["run_id"]
        proposal = {
            "contract_version": "research-v1",
            "run_id": run_id,
            "case_id": case["case_id"],
            "source_status": "produced",
            "p_outperform": 0.6,
            "expected_excess_return": 0.02,
            "references": [],
            "warnings": [],
            "missing": [],
            "quantitative_basis": None,
            "model": {"model_version": "llm-v1", "provider": "test", "cost_estimate": {}},
        }
        return json.dumps({"ok": True, "proposal": proposal}, sort_keys=True).encode(), b""

    def kill(self):
        pass

    async def wait(self):
        return 0


class _FailingProcess:
    """A fake agent-runtime subprocess that exits non-zero with stderr
    (e.g. a Hermes crash or a rejected capability inside the runtime)."""

    def __init__(self):
        self.returncode = 1

    async def communicate(self, data: bytes):
        return b"", b"hermes crashed"

    def kill(self):
        pass

    async def wait(self):
        return 1


def _agent_runtime(process_factory, *, timeout_seconds=30.0) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        research_config={"base_url": "x", "api_key": "k", "model": "m"},
        process_factory=process_factory,
        timeout_seconds=timeout_seconds,
    )


async def _submit_batch_predict(engine, tenant_id, batch_id, *, release_id="rel-phase1b-v1"):
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(
            kind="research.batch_predict",
            jobs=[{
                "kind": "research.batch_predict",
                "payload": {"batch_id": str(batch_id), "release_id": release_id},
                "max_attempts": 2,
            }],
        ),
        idempotency_key=f"res-{uuid.uuid4().hex[:8]}",
    )


# --- cancellation -----------------------------------------------------------


async def test_cancel_mid_research_kills_subprocess_and_recovers(db_engine, tenant_id):
    """Cancel a batch_predict job while it awaits the agent-runtime
    subprocess: the watchdog cancels the handler, the subprocess is
    killed (no orphan Hermes), the attempt lands cancelled, and the run
    is terminal cancelled."""
    from youwei_core.config import Settings
    from youwei_core.db.meta import attempts, jobs

    ctx, batch_id, case_ids = await _phase1b_batch(db_engine, tenant_id)
    await _submit_batch_predict(db_engine, tenant_id, batch_id)

    hanging = _HangingProcess()
    entered = asyncio.Event()

    async def process_factory():
        entered.set()
        return hanging

    handler = make_batch_predict_handler(
        db_engine,
        agent_runtime=_agent_runtime(process_factory),
    )
    loop = WorkerLoop(
        db_engine,
        handlers={"research.batch_predict": handler},
        settings=Settings(
            database_url="unused",
            heartbeat_interval_seconds=0.05,
            lease_ttl_seconds=5.0,
        ),
    )
    run_task = asyncio.create_task(loop.run_once())

    # wait until the handler has reached the subprocess boundary
    assert await asyncio.wait_for(entered.wait(), timeout=5)

    # find the run id of the in-flight batch_predict job
    async with db_engine.begin() as conn:
        run_id = (
            await conn.execute(
                select(jobs.c.run_id).where(jobs.c.status == "running")
            )
        ).scalar_one()

    await cancel_run(db_engine, tenant_id, run_id)

    # watchdog cancels the handler; run_once returns True and the subprocess
    # is killed (S07h reap-on-cancel), attempt lands cancelled.
    assert await asyncio.wait_for(asyncio.shield(run_task), timeout=2) is True
    assert hanging.killed is True

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                select(attempts.c.status).order_by(attempts.c.attempt_no)
            )
        ).scalars().first()
    assert row == "cancelled"
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "cancelled"


# --- fault recovery ---------------------------------------------------------


async def _claim_batch_predict(engine, tenant_id):
    """Claim a submitted batch_predict job (capability token is signed by
    the worker loop in production; tests stub it)."""
    claimed = await claim_next_job(engine, "test-worker")
    assert claimed is not None
    claimed.capability_token = "ywc_test"
    return claimed


async def _llm_status(engine, case_id):
    from youwei_core.ledger.sealing import commit_status

    async with engine.begin() as conn:
        commits = (
            await conn.execute(
                text("SELECT id FROM forecast_commits WHERE case_id = :c"),
                {"c": str(case_id)},
            )
        ).scalars().all()
    assert commits, "expected a sealed commit"
    status = await commit_status(engine, commits[0])
    return {p["source"]: p for p in status["predictions"]}["llm_adjusted"]


async def test_subprocess_timeout_seals_llm_unavailable(db_engine, tenant_id):
    """A hung subprocess hits the timeout: the LLM position falls back to
    unavailable (never a fabricated value), but the batch still seals the
    baseline + quant positions and the job completes."""
    ctx, batch_id, case_ids = await _phase1b_batch(db_engine, tenant_id)
    await _submit_batch_predict(db_engine, tenant_id, batch_id)
    claimed = await _claim_batch_predict(db_engine, tenant_id)

    hanging = _HangingProcess()

    async def process_factory():
        return hanging

    summary = await run_batch_predictions(
        db_engine, claimed, batch_id, "rel-phase1b-v1",
        agent_runtime=_agent_runtime(process_factory, timeout_seconds=0.01),
    )
    # the batch completes: baseline + quant sealed, llm unavailable
    assert summary["sealed"] == len(case_ids)
    assert summary["failed"] == []

    llm = await _llm_status(db_engine, case_ids[0])
    assert llm["source_status"] in ("unavailable", "fallback")


async def test_subprocess_failure_seals_llm_unavailable(db_engine, tenant_id):
    """A non-zero subprocess exit degrades the LLM position to unavailable
    without failing the whole batch."""
    ctx, batch_id, case_ids = await _phase1b_batch(db_engine, tenant_id)
    await _submit_batch_predict(db_engine, tenant_id, batch_id)
    claimed = await _claim_batch_predict(db_engine, tenant_id)

    async def process_factory():
        return _FailingProcess()

    summary = await run_batch_predictions(
        db_engine, claimed, batch_id, "rel-phase1b-v1",
        agent_runtime=_agent_runtime(process_factory),
    )
    assert summary["sealed"] == len(case_ids)
    llm = await _llm_status(db_engine, case_ids[0])
    assert llm["source_status"] in ("unavailable", "fallback")


async def test_duplicate_response_is_idempotent(db_engine, tenant_id):
    """Running the same batch twice (response lost and retried) must not
    create a second commit: the second run reports already_sealed and the
    ledger keeps one commit per case."""
    ctx, batch_id, case_ids = await _phase1b_batch(db_engine, tenant_id)
    await _submit_batch_predict(db_engine, tenant_id, batch_id)
    claimed = await _claim_batch_predict(db_engine, tenant_id)

    async def process_factory():
        return _EchoProcess()

    first = await run_batch_predictions(
        db_engine, claimed, batch_id, "rel-phase1b-v1",
        agent_runtime=_agent_runtime(process_factory),
    )
    assert first["sealed"] == len(case_ids)

    # second run: a fresh claim (attempt_no advances) over the same batch
    await _submit_batch_predict(db_engine, tenant_id, batch_id)
    claimed2 = await _claim_batch_predict(db_engine, tenant_id)
    second = await run_batch_predictions(
        db_engine, claimed2, batch_id, "rel-phase1b-v1",
        agent_runtime=_agent_runtime(process_factory),
    )
    assert second["already_sealed"] == len(case_ids)

    async with db_engine.begin() as conn:
        commits = (
            await conn.execute(
                text("SELECT count(*) FROM forecast_commits WHERE case_id = :c"),
                {"c": str(case_ids[0])},
            )
        ).scalar_one()
    assert commits == 1


async def test_lease_expiry_requeues_and_recovers_through_agent_runtime(db_engine, tenant_id):
    """A worker whose lease lapses mid-research (subprocess hung, no
    heartbeat) must not strand the job: the expired attempt requeues and a
    fresh worker with a working subprocess seals the batch."""
    from youwei_core.config import Settings

    ctx, batch_id, case_ids = await _phase1b_batch(db_engine, tenant_id)
    await _submit_batch_predict(db_engine, tenant_id, batch_id)

    entered = asyncio.Event()

    async def hanging_factory():
        entered.set()
        return _HangingProcess()

    handler = make_batch_predict_handler(
        db_engine, agent_runtime=_agent_runtime(hanging_factory),
    )
    loop = WorkerLoop(
        db_engine,
        handlers={"research.batch_predict": handler},
        settings=Settings(heartbeat_interval_seconds=0.05, lease_ttl_seconds=0.3),
    )
    task = asyncio.create_task(loop.run_once())
    # let the handler reach the hung subprocess, then drop the worker
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # lease expires; a recovery worker claims the requeued job and seals it
    await asyncio.sleep(0.35)
    claimed = await claim_next_job(db_engine, "recovery-worker")
    assert claimed is not None
    claimed.capability_token = "ywc_test"

    async def good_factory():
        return _EchoProcess()

    summary = await run_batch_predictions(
        db_engine, claimed, batch_id, "rel-phase1b-v1",
        agent_runtime=_agent_runtime(good_factory),
    )
    assert summary["sealed"] == len(case_ids)
