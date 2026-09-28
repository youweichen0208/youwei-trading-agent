"""S07g: Phase 1B campaign registration and three-source sealing (DB level).

The Phase 1B policy (enabled sources baseline + quant_model + llm_adjusted,
phase1b-llm-from-quant fallback) is now registered in the sealing path. These
tests exercise the full registration + seal flow against a real PostgreSQL:
register a Phase 1B campaign, plan a case, and seal three positions where the
llm_adjusted position is produced (LLM success) or fallback (LLM failure
copies quant), then verify the ledger rows.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from youwei_core.auth.service import create_tenant
from youwei_core.data.calendar import build_calendar, next_weekly_cutoff
from youwei_core.data.snapshots import freeze_daily_bars
from youwei_core.jobs.service import RunSubmission, submit_run
from youwei_core.jobs.worker import claim_next_job
from youwei_core.ledger.service import (
    approve_release,
    plan_batch,
    register_campaign,
    register_release,
)
from youwei_core.ledger.sealing import (
    SealRequest,
    SourcePrediction,
    commit_status,
    seal_commit,
)
from test_data_pit import _security
from test_ledger_seal import _quant_evidence_for, _shift_case_window

SPEC_SHA = "a" * 64
TIME_SHA = "b" * 64


def _specs():
    return [
        {"horizon_td": 1, "target_spec_id": "excess-tr-d1-v1", "content_sha256": SPEC_SHA},
        {"horizon_td": 20, "target_spec_id": "excess-tr-d20-v1", "content_sha256": SPEC_SHA},
        {"horizon_td": 60, "target_spec_id": "excess-tr-d60-v1", "content_sha256": SPEC_SHA},
    ]


async def _phase1b_release(engine, *, release_id="rel-phase1b-v1"):
    manifest = {
        "enabled_sources": ["baseline", "quant_model", "llm_adjusted"],
        "fallback_policy": "phase1b-llm-from-quant",
        "prompt_version": "candidate-v0",  # placeholder; real prompt is Trial-registered
    }
    await register_release(engine, release_id=release_id, manifest=manifest)
    await approve_release(
        engine,
        release_id=release_id,
        approver_principal_id="human-owner",
        scope="phase1b-forward",
    )


async def _phase1b_setup(engine, tenant_id, *, n_panel=1):
    """Calendar + securities + approved Phase 1B release + campaign."""
    await build_calendar(engine, year_start=2024, year_end=datetime.now(UTC).year + 2)
    benchmark = await _security(engine, ticker="SPY")
    panel = [await _security(engine, ticker=f"S{i}") for i in range(n_panel)]
    await _phase1b_release(engine)
    await create_tenant(engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    campaign = await register_campaign(
        engine,
        tenant_id=tenant_id,
        campaign_key="c-phase1b",
        release_id="rel-phase1b-v1",
        target_specs=_specs(),
        time_protocol_ref="time-protocol-v1",
        time_protocol_sha256=TIME_SHA,
        benchmark_security_id=benchmark,
        panel_security_ids=[str(s) for s in panel],
        panel_manifest={"sampler_version": "sector-stratified-hash-v1"},
        enabled_sources=["baseline", "quant_model", "llm_adjusted"],
        fallback_policy="phase1b-llm-from-quant",
    )
    return {"benchmark": benchmark, "panel": panel, "campaign": campaign}


async def _phase1b_case(engine, tenant_id):
    """Register + plan a Phase 1B campaign, open one case's window."""
    ctx = await _phase1b_setup(engine, tenant_id)
    plan = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    case_id = plan.case_ids[0]
    await _shift_case_window(engine, case_id, (timedelta(hours=-1), timedelta(hours=1)))
    return ctx, case_id


async def _attempt(engine, tenant_id):
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(
            kind="research.seal",
            total_budget_micros=0,
            jobs=[{"kind": "research.seal", "payload": {}, "max_attempts": 1}],
        ),
        idempotency_key=f"idem-{uuid.uuid4()}",
    )
    return await claim_next_job(engine, "test-worker")


def _sources(llm_status, *, llm_p=0.6, llm_reason=None, llm_model="llm-v1"):
    return [
        SourcePrediction(
            source="baseline", source_status="produced",
            p_outperform=0.5, expected_excess_return=0.0, model_version="baseline-v0",
        ),
        SourcePrediction(
            source="quant_model", source_status="produced",
            p_outperform=0.4, expected_excess_return=0.01, model_version="quant-v0",
        ),
        SourcePrediction(
            source="llm_adjusted", source_status=llm_status, reason=llm_reason,
            p_outperform=llm_p if llm_status != "unavailable" else None,
            expected_excess_return=0.02 if llm_status != "unavailable" else None,
            model_version=llm_model,
        ),
    ]


async def _seal_phase1b(engine, tenant_id, case_id, sources):
    claimed = await _attempt(engine, tenant_id)
    evidence = await _quant_evidence_for(engine, case_id)
    sources = [
        s.model_copy(update={"evidence_snapshot_id": evidence})
        if s.source in ("quant_model", "llm_adjusted") and s.evidence_snapshot_id is None
        else s
        for s in sources
    ]
    request = SealRequest(
        case_id=case_id,
        release_id="rel-phase1b-v1",
        sources=sources,
        input_manifest={"code_version": "seal-phase1b-test-v1"},
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
    )
    return await seal_commit(engine, request)


async def test_phase1b_campaign_registers_with_fallback_policy(db_engine, tenant_id):
    ctx = await _phase1b_setup(db_engine, tenant_id)
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT enabled_sources, fallback_policy FROM campaigns WHERE id = :c"),
                {"c": str(ctx["campaign"].campaign_id)},
            )
        ).mappings().one()
    assert row.enabled_sources == ["baseline", "llm_adjusted", "quant_model"]
    assert row.fallback_policy == "phase1b-llm-from-quant"


async def test_phase1b_seals_produced_llm_adjusted(db_engine, tenant_id):
    ctx, case_id = await _phase1b_case(db_engine, tenant_id)
    result = await _seal_phase1b(
        db_engine, tenant_id, case_id, _sources("produced", llm_p=0.6)
    )
    assert result.created

    status = await commit_status(db_engine, result.commit_id)
    by_source = {p["source"]: p for p in status["predictions"]}
    llm = by_source["llm_adjusted"]
    assert llm["source_status"] == "produced"
    assert llm["p_outperform"] == "0.6000000000"


async def test_phase1b_seals_fallback_copied_from_quant(db_engine, tenant_id):
    ctx, case_id = await _phase1b_case(db_engine, tenant_id)
    # llm_adjusted fallback must carry the quant value (0.4) and a reason
    sources = _sources("fallback", llm_p=0.4, llm_reason="timeout", llm_model="quant-v0")
    result = await _seal_phase1b(db_engine, tenant_id, case_id, sources)
    assert result.created

    status = await commit_status(db_engine, result.commit_id)
    by_source = {p["source"]: p for p in status["predictions"]}
    llm = by_source["llm_adjusted"]
    assert llm["source_status"] == "fallback"
    assert llm["p_outperform"] == "0.4000000000"
    assert llm["reason"] == "timeout"
