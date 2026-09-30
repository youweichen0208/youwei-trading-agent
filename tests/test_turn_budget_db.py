"""S07k: turn-level budget wiring around the agent-runtime fetcher (DB-level).

End-to-end: the fetcher reserves a configured upper bound before spawning
the subprocess, then settles the priced usage after a complete turn-level
report. Placeholder rates settle to estimated_micros (never settled_micros);
an incomplete/unknown report leaves the reservation open for reconciliation.
"""

import asyncio
import hashlib
import json
import uuid

from youwei_core.jobs.service import JobSubmission, RunSubmission, get_run_view, submit_run
from youwei_core.ledger.pipeline import (
    AgentRuntimeConfig,
    TurnBudgetWiring,
    make_phase1b_llm_fetcher,
)
from youwei_core.ledger.evidence import build_frozen_evidence


def _snapshot(content: list[dict]) -> dict:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "id": str(uuid.uuid4()),
        "manifest": {
            "kind": "daily_bars",
            "query": {"kind": "daily_bars", "as_of": "2026-09-26T10:00:00+00:00", "mode": "forward"},
            "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "code_version": "daily-bars-snapshot-v1",
        },
        "content": canonical,
    }


def _case(security_id: uuid.UUID) -> dict:
    from datetime import UTC, datetime
    return {
        "id": uuid.uuid4(),
        "security_id": security_id,
        "benchmark_security_id": uuid.uuid4(),
        "horizon_td": 20,
        "target_spec_id": "excess-tr-d20-v1",
        "target_spec_sha256": "a" * 64,
        "decision_cutoff_utc": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "prediction_deadline_utc": datetime(2026, 9, 28, 13, 15, tzinfo=UTC),
        "entry_at_utc": datetime(2026, 9, 28, 13, 30, tzinfo=UTC),
        "exit_at_utc": datetime(2026, 10, 23, 20, 0, tzinfo=UTC),
    }


class _EchoUsageProcess:
    """A fake agent-runtime subprocess that answers with a produced proposal
    bound to the requested case, plus a usage report."""

    def __init__(self, usage_report: dict):
        self.usage_report = usage_report
        self.returncode = 0

    async def communicate(self, data: bytes):
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
        result = json.dumps({"ok": True, "proposal": proposal, "usage": self.usage_report})
        return result.encode(), b""

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _complete_usage() -> dict:
    return {
        "source": "session_delta",
        "scope": "chat_turn",
        "complete": True,
        "incomplete_reasons": [],
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "api_calls": 1,
    }


async def _make_run(db_engine, tenant_id, total=1_000_000):
    submission = RunSubmission(
        kind="research",
        total_budget_micros=total,
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    result = await submit_run(db_engine, tenant_id, submission, f"budget-{uuid.uuid4().hex[:8]}")
    return result.run_id


async def _fetch_with_budget(db_engine, run_id, tenant_id, attempt_id, usage_report):
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(uuid.UUID(sec))
    evidence = build_frozen_evidence(
        run_id=run_id, tenant_id=tenant_id, case=case, snapshot=snap, batch_manifest={},
    )
    # Build the fetcher with a budget wiring; the process factory returns a
    # fake subprocess carrying the requested usage report.
    budget = TurnBudgetWiring(
        engine=db_engine,
        attempt_id=attempt_id,
        turn_reserve_micros=50_000,
    )

    async def process_factory():
        return _EchoUsageProcess(usage_report)

    fetch = make_phase1b_llm_fetcher(
        run_id=run_id,
        tenant_id=tenant_id,
        snapshot=snap,
        batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={"base_url": "x", "api_key": "k", "model": "glm-5.3"},
            process_factory=process_factory,
            timeout_seconds=30.0,
        ),
        budget=budget,
    )
    return await fetch(case, bars), case, evidence


async def test_fetcher_reserves_then_settles_estimated(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)
    attempt_id = uuid.uuid4()
    proposal, _, _ = await _fetch_with_budget(
        db_engine, run_id, tenant_id, attempt_id, _complete_usage(),
    )
    assert proposal.source_status == "produced"

    view = await get_run_view(db_engine, tenant_id, run_id)
    # glm-5.3 is a placeholder rate: settled as estimated, reservation released.
    # 100*10 + 50*40 = 3000 micros.
    assert view["settled_micros"] == 0
    assert view["estimated_micros"] == 3000
    assert view["reserved_micros"] == 0


async def test_fetcher_leaves_reservation_open_on_incomplete_usage(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)
    attempt_id = uuid.uuid4()
    incomplete = _complete_usage()
    incomplete["complete"] = False
    incomplete["incomplete_reasons"] = ["turn_failed"]
    proposal, _, _ = await _fetch_with_budget(
        db_engine, run_id, tenant_id, attempt_id, incomplete,
    )
    assert proposal.source_status == "produced"

    view = await get_run_view(db_engine, tenant_id, run_id)
    # unknown cost: the reservation stays open (pending reconciliation).
    assert view["settled_micros"] == 0
    assert view["estimated_micros"] == 0
    assert view["reserved_micros"] == 50_000


async def test_fetcher_leaves_reservation_open_on_unavailable_usage(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)
    attempt_id = uuid.uuid4()
    proposal, _, _ = await _fetch_with_budget(
        db_engine, run_id, tenant_id, attempt_id,
        {"source": "unavailable", "scope": "unknown", "complete": False,
         "incomplete_reasons": ["no_usage_signal"]},
    )
    assert proposal.source_status == "produced"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["settled_micros"] == 0
    assert view["estimated_micros"] == 0
    assert view["reserved_micros"] == 50_000
