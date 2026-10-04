"""S06i: frozen campaign plan range, plan-bound approval scope, stop control.

DB-level acceptance for the forward-experiment boundaries:
- a batch may only be planned for a cutoff inside the frozen weekly list
- registration requires an approval whose structured scope binds the exact
  plan (campaign_plan_sha256 + tenant + campaign key + release hash)
- stop_new_batches is an append-only control event that halts new planning/
  prediction dispatch but leaves outcome follow-up independent
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from campaign_plan_helper import make_campaign_plan
from youwei_core.data.calendar import build_calendar, next_weekly_cutoff
from youwei_core.db.meta import campaign_control_events, campaigns, research_releases
from youwei_core.ledger.service import (
    CampaignValidationError,
    PlanError,
    approve_release,
    campaign_is_stopped,
    plan_batch,
    register_campaign,
    stop_campaign_new_batches,
)
from test_ledger_campaign import (
    TIME_SHA,
    _campaign,
    _release,
    _security,
    _specs,
)

from youwei_core.auth.service import create_tenant


async def _ctx(engine, tenant_id, *, n_panel=2):
    await build_calendar(engine, year_start=2024, year_end=2027)
    benchmark = await _security(engine, ticker="SPY")
    panel = [await _security(engine, ticker=f"S{i}") for i in range(n_panel)]
    await _release(engine)
    await create_tenant(engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    campaign = await _campaign(engine, tenant_id, benchmark, panel)
    return {"benchmark": benchmark, "panel": panel, "campaign": campaign}


async def test_plan_batch_rejects_cutoff_outside_frozen_plan(db_engine, tenant_id):
    ctx = await _ctx(db_engine, tenant_id)
    campaign_id = ctx["campaign"].campaign_id

    async with db_engine.begin() as conn:
        planned = (
            await conn.execute(
                select(campaigns.c.planned_cutoffs).where(campaigns.c.id == campaign_id)
            )
        ).scalar_one()

    # a cutoff one week after the last frozen cutoff is outside the plan
    last = datetime.fromisoformat(planned[-1])
    outside = last + timedelta(days=7)
    with pytest.raises(PlanError, match="outside the campaign's frozen plan"):
        await plan_batch(db_engine, campaign_id, decision_cutoff=outside)


async def test_register_rejects_wrong_plan_scope(db_engine, tenant_id):
    """A plan-bound approval must match tenant + campaign key + plan hash; a
    wrong campaign_plan_sha256 (or a different plan) is rejected."""
    await build_calendar(db_engine, year_start=2024, year_end=2027)
    benchmark = await _security(db_engine, ticker="SPY")
    panel = [await _security(db_engine, ticker="S0")]
    await _release(db_engine)
    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)

    async with db_engine.begin() as conn:
        release_sha = (
            await conn.execute(
                select(research_releases.c.release_content_sha256).where(
                    research_releases.c.release_id == "rel-test-v1"
                )
            )
        ).scalar_one()

    plan, scope = make_campaign_plan(
        tenant_id=tenant_id, campaign_key="c-s", release_content_sha256=release_sha,
        benchmark_security_id=benchmark, panel_security_ids=panel,
        target_specs=_specs(), time_protocol_sha256=TIME_SHA,
    )
    # approve with a DIFFERENT plan hash (scope binds the wrong plan)
    other, other_scope = make_campaign_plan(
        tenant_id=tenant_id, campaign_key="c-other", release_content_sha256=release_sha,
        benchmark_security_id=benchmark, panel_security_ids=panel,
        target_specs=_specs(), time_protocol_sha256=TIME_SHA,
    )
    await approve_release(
        db_engine, release_id="rel-test-v1", approver_principal_id="human-owner",
        scope="phase1a-forward", scope_manifest=other_scope.model_dump(mode="json"),
    )
    # register with a SELF-CONSISTENT plan ("c-s") whose hash the approval does
    # NOT bind (it bound "c-other") -> rejected at the approval gate
    with pytest.raises(CampaignValidationError, match="approval"):
        await register_campaign(
            db_engine, tenant_id=tenant_id, campaign_key="c-s", release_id="rel-test-v1",
            target_specs=_specs(), time_protocol_ref="time-protocol-v1", time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark, panel_security_ids=[str(s) for s in panel],
            panel_manifest={}, enabled_sources=["baseline", "quant_model"],
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )


async def test_stop_new_batches_idempotent_and_recorded(db_engine, tenant_id):
    ctx = await _ctx(db_engine, tenant_id)
    campaign_id = ctx["campaign"].campaign_id

    assert await stop_campaign_new_batches(
        db_engine, campaign_id, reason="experiment ended", actor_principal_id="human-owner"
    ) is True
    assert await campaign_is_stopped(db_engine, campaign_id) is True
    # idempotent: a second stop is a no-op, no second event row
    assert await stop_campaign_new_batches(
        db_engine, campaign_id, reason="again", actor_principal_id="human-owner"
    ) is False
    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                select(campaign_control_events).where(
                    campaign_control_events.c.campaign_id == campaign_id
                )
            )
        ).mappings().all()
    assert len(rows) == 1
    assert rows[0].event_type == "stop_new_batches"
    assert rows[0].reason == "experiment ended"


async def test_scheduler_skips_stopped_campaign_planning(db_engine, tenant_id):
    """After stop, the scheduler tick plans no NEW batches for that campaign."""
    from youwei_core.ledger.scheduler import scheduler_tick

    ctx = await _ctx(db_engine, tenant_id)
    campaign_id = ctx["campaign"].campaign_id

    # plan everything up to the current target first (so we can observe the
    # stop suppressing FUTURE planning), then stop
    await scheduler_tick(db_engine)
    await stop_campaign_new_batches(
        db_engine, campaign_id, reason="stop", actor_principal_id="human-owner"
    )
    summary = await scheduler_tick(db_engine)
    assert str(campaign_id) not in {
        e.get("campaign_id") for e in summary["errors"]
        if e.get("phase") == "planning"
    }
    # a stopped campaign contributes no new planned batches
    assert all(
        not (e.get("campaign_id") == str(campaign_id))
        for e in summary["errors"]
    )


async def _approved_campaign_material(db_engine, tenant_id):
    """Build an approved release + a plan-bound approval scope, returning the
    materials a register_campaign call needs (release_sha, plan, scope)."""
    await build_calendar(db_engine, year_start=2024, year_end=2027)
    benchmark = await _security(db_engine, ticker="SPY")
    panel = [await _security(db_engine, ticker="S0")]
    await _release(db_engine)
    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    async with db_engine.begin() as conn:
        release_sha = (
            await conn.execute(
                select(research_releases.c.release_content_sha256).where(
                    research_releases.c.release_id == "rel-test-v1"
                )
            )
        ).scalar_one()
    plan, scope = make_campaign_plan(
        tenant_id=tenant_id, campaign_key="c-s", release_content_sha256=release_sha,
        benchmark_security_id=benchmark, panel_security_ids=panel,
        target_specs=_specs(), time_protocol_sha256=TIME_SHA,
    )
    await approve_release(
        db_engine, release_id="rel-test-v1", approver_principal_id="human-owner",
        scope="phase1a-forward", scope_manifest=scope.model_dump(mode="json"),
    )
    return benchmark, panel, plan


async def test_register_recomputes_plan_hash_and_rejects_changed_primary_metric(db_engine, tenant_id):
    """S06i hardening: changing the primary metric while reusing the approved
    campaign_plan_sha256 must be rejected — the server recomputes the hash
    from the actual params, it does not trust the caller's string."""
    benchmark, panel, plan = await _approved_campaign_material(db_engine, tenant_id)
    with pytest.raises(CampaignValidationError, match="campaign_plan_sha256 does not match"):
        await register_campaign(
            db_engine, tenant_id=tenant_id, campaign_key="c-s", release_id="rel-test-v1",
            target_specs=_specs(), time_protocol_ref="time-protocol-v1", time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark, panel_security_ids=[str(s) for s in panel],
            panel_manifest={}, enabled_sources=["baseline", "quant_model"],
            # the plan hash was computed for the DEFAULT primary metric; a
            # different metric changes the real plan but not the reused hash
            primary_metric="d1_paired_brier_delta",
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )


async def test_register_recomputes_plan_hash_and_rejects_changed_target_specs(db_engine, tenant_id):
    """Changing a target spec while reusing the approved hash is rejected."""
    benchmark, panel, plan = await _approved_campaign_material(db_engine, tenant_id)
    # keep all three horizons (so we pass _validate_target_specs) but change
    # the D20 content hash — the real plan differs, the reused hash does not.
    tampered_specs = [dict(s) for s in _specs()]
    tampered_specs[1]["content_sha256"] = "0" * 64
    with pytest.raises(CampaignValidationError, match="campaign_plan_sha256 does not match"):
        await register_campaign(
            db_engine, tenant_id=tenant_id, campaign_key="c-s", release_id="rel-test-v1",
            target_specs=tampered_specs, time_protocol_ref="time-protocol-v1", time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark, panel_security_ids=[str(s) for s in panel],
            panel_manifest={}, enabled_sources=["baseline", "quant_model"],
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )


async def test_register_recomputes_plan_hash_and_rejects_changed_sources(db_engine, tenant_id):
    """Registering a Phase 1B source set against a Phase 1A approval (same hash
    string) is rejected by the server-side recomputation."""
    benchmark, panel, plan = await _approved_campaign_material(db_engine, tenant_id)
    with pytest.raises(CampaignValidationError, match="campaign_plan_sha256 does not match"):
        await register_campaign(
            db_engine, tenant_id=tenant_id, campaign_key="c-s", release_id="rel-test-v1",
            target_specs=_specs(), time_protocol_ref="time-protocol-v1", time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark, panel_security_ids=[str(s) for s in panel],
            panel_manifest={}, enabled_sources=["baseline", "quant_model", "llm_adjusted"],
            fallback_policy="phase1b-llm-from-quant",
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )
