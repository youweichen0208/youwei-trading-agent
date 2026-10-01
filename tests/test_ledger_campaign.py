"""S05a: campaign registration and batch planning.

Acceptance mapped from the plan (S05) and campaign-policy v1:
- a campaign is pre-registered against a human-approved release with
  fixed panel, target specs and Phase 1A sources
- batches resolve concrete cutoff/deadline/entry/exit times from the
  versioned calendar and seal the planned case windows
- missed weeks are recorded as backfilled plans, never silently
  skipped
- ledger records are append-only at the database level
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from conftest import make_campaign_plan
from youwei_core.data.calendar import (
    ET,
    CalendarError,
    build_calendar,
    next_weekly_cutoff,
)
from youwei_core.ledger import service as ledger_service
from youwei_core.ledger.service import (
    CampaignConflict,
    CampaignValidationError,
    PlanBackfillRequired,
    approve_release,
    plan_batch,
    record_batch_miss,
    register_campaign,
    register_release,
)
from youwei_core.db.meta import research_releases
from test_data_pit import _security

# protocol file hashes are only placeholders for tests: real values
# are registered when the actual campaign starts (S06)
SPEC_SHA = "a" * 64
TIME_SHA = "b" * 64

# Frozen-plan anchor for tests whose cutoffs land in Sept/Oct 2026 (the
# S04b known-answer date 2026-09-26 and next_weekly_cutoff(now)). The frozen
# weekly list must CONTAIN those cutoffs or plan_batch rejects them as out of
# scope.
FIRST_CUTOFF_2026Q4 = datetime(2026, 9, 5, 6, 0, tzinfo=ET)  # Saturday 06:00 ET


def _specs():
    return [
        {"horizon_td": 1, "target_spec_id": "excess-tr-d1-v1", "content_sha256": SPEC_SHA},
        {"horizon_td": 20, "target_spec_id": "excess-tr-d20-v1", "content_sha256": SPEC_SHA},
        {"horizon_td": 60, "target_spec_id": "excess-tr-d60-v1", "content_sha256": SPEC_SHA},
    ]


def _plan_hashes():
    """A self-consistent frozen plan for tests that assert OTHER validation
    errors — the cutoff list and its hash satisfy the range gate so the test
    reaches the check it actually exercises."""
    from youwei_core.ledger.plan import prepare_campaign_plan

    plan = prepare_campaign_plan(
        tenant_id=uuid.uuid4(),
        campaign_key="plan",
        release_content_sha256="a" * 64,
        phase="1a",
        first_cutoff=datetime(2026, 1, 3, 6, 0, tzinfo=ET),
        batch_count=12,
        panel_security_ids=[str(uuid.uuid4())],
        benchmark_security_id=str(uuid.uuid4()),
        target_specs=_specs(),
        enabled_sources=["baseline", "quant_model"],
        fallback_policy="phase1a-none",
        primary_metric="d20_paired_brier_delta",
        time_protocol_ref="time-protocol-v1",
        time_protocol_sha256=TIME_SHA,
    )
    return dict(
        planned_cutoffs=plan["planned_cutoffs"],
        planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )


async def _build_calendar(engine):
    now = datetime.now(UTC)
    return await build_calendar(engine, year_start=2024, year_end=now.year + 2)


async def _release(engine, *, release_id="rel-test-v1", approved=True, manifest=None):
    manifest = manifest or {
        "enabled_sources": ["baseline", "quant_model"],
        "fallback_policy": "phase1a-none",
        "prompt_version": "not_enabled",
    }
    rec = await register_release(engine, release_id=release_id, manifest=manifest)
    if approved:
        await approve_release(
            engine,
            release_id=release_id,
            approver_principal_id="human-owner",
            scope="phase1a-forward",
        )
    return rec


async def _campaign(engine, tenant_id, benchmark, panel, *, release_id="rel-test-v1", first_cutoff=None, batch_count=12):
    from youwei_core.auth.service import create_tenant

    await create_tenant(engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    async with engine.begin() as conn:
        release_sha = (
            await conn.execute(
                select(research_releases.c.release_content_sha256).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).scalar_one()
    plan, scope = make_campaign_plan(
        tenant_id=tenant_id,
        campaign_key="c-test-1",
        release_content_sha256=release_sha,
        benchmark_security_id=benchmark,
        panel_security_ids=[str(s) for s in panel],
        target_specs=_specs(),
        time_protocol_sha256=TIME_SHA,
        first_cutoff=first_cutoff,
        batch_count=batch_count,
    )
    await approve_release(
        engine,
        release_id=release_id,
        approver_principal_id="human-owner",
        scope="phase1a-forward",
        scope_manifest=scope.model_dump(mode="json"),
    )
    return await register_campaign(
        engine,
        tenant_id=tenant_id,
        campaign_key="c-test-1",
        release_id=release_id,
        target_specs=_specs(),
        time_protocol_ref="time-protocol-v1",
        time_protocol_sha256=TIME_SHA,
        benchmark_security_id=benchmark,
        panel_security_ids=[str(s) for s in panel],
        panel_manifest={"sampler_version": "sector-stratified-hash-v1"},
        enabled_sources=["baseline", "quant_model"],
        planned_cutoffs=plan["planned_cutoffs"],
        planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )


async def _setup(engine, tenant_id, *, n_panel=2, first_cutoff=None, batch_count=12):
    """Calendar + securities + approved release + campaign."""
    await _build_calendar(engine)
    benchmark = await _security(engine, ticker="SPY")
    panel = [await _security(engine, ticker=f"S{i}") for i in range(n_panel)]
    await _release(engine)
    campaign = await _campaign(engine, tenant_id, benchmark, panel, first_cutoff=first_cutoff, batch_count=batch_count)
    return {"benchmark": benchmark, "panel": panel, "campaign": campaign}


# --- releases ----------------------------------------------------------------


async def test_release_registration_idempotent_and_conflict(db_engine):
    a = await register_release(db_engine, release_id="rel-a", manifest={"v": 1})
    b = await register_release(db_engine, release_id="rel-a", manifest={"v": 1})
    assert a.created and not b.created
    assert a.release_row_id == b.release_row_id
    with pytest.raises(ledger_service.ReleaseConflict):
        await register_release(db_engine, release_id="rel-a", manifest={"v": 2})


async def test_release_content_hash_deterministic_and_self_excluding(db_engine):
    h1 = ledger_service.release_content_hash({"b": 2, "a": 1})
    h2 = ledger_service.release_content_hash({"a": 1, "b": 2, "release_content_hash": "x"})
    assert h1 == h2 and len(h1) == 64


async def test_campaign_requires_plan_bound_approval(db_engine, tenant_id):
    """A legacy free-text approval does NOT authorize a campaign: the approval
    must carry a structured scope whose campaign_plan_sha256 binds this exact
    plan (S06i)."""
    await _build_calendar(db_engine)
    benchmark = await _security(db_engine, ticker="SPY")
    panel = [await _security(db_engine, ticker="S0")]
    await _release(db_engine, approved=False)
    # legacy free-text approval only -> still rejected (no plan-bound scope)
    await approve_release(
        db_engine,
        release_id="rel-test-v1",
        approver_principal_id="human-owner",
        scope="phase1a-forward",
    )
    async with db_engine.begin() as conn:
        release_sha = (
            await conn.execute(
                select(research_releases.c.release_content_sha256).where(
                    research_releases.c.release_id == "rel-test-v1"
                )
            )
        ).scalar_one()
    plan, scope = make_campaign_plan(
        tenant_id=tenant_id,
        campaign_key="c-test-1",
        release_content_sha256=release_sha,
        benchmark_security_id=benchmark,
        panel_security_ids=panel,
        target_specs=_specs(),
        time_protocol_sha256=TIME_SHA,
    )
    from youwei_core.auth.service import create_tenant

    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    with pytest.raises(CampaignValidationError, match="approval"):
        await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key="c-test-1",
            release_id="rel-test-v1",
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark,
            panel_security_ids=[str(s) for s in panel],
            panel_manifest={"sampler_version": "sector-stratified-hash-v1"},
            enabled_sources=["baseline", "quant_model"],
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )
    # _campaign approves with the plan-bound scope then registers -> succeeds
    assert (await _campaign(db_engine, tenant_id, benchmark, panel)).created


async def test_approve_unknown_release_rejected(db_engine):
    with pytest.raises(ledger_service.ReleaseNotFound):
        await approve_release(
            db_engine,
            release_id="nope",
            approver_principal_id="human-owner",
            scope="x",
        )


# --- campaigns ----------------------------------------------------------------


async def test_campaign_registration_stores_plan_and_chain(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id)
    assert ctx["campaign"].created

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT * FROM campaigns WHERE id = :id"),
                {"id": str(ctx["campaign"].campaign_id)},
            )
        ).mappings().one()
        chain = (
            await conn.execute(
                text("SELECT * FROM ledger_chains WHERE chain_id = :c"),
                {"c": f"campaign:{ctx['campaign'].campaign_id}"},
            )
        ).mappings().one()
    assert row.enabled_sources == ["baseline", "quant_model"]
    assert row.fallback_policy == "phase1a-none"
    assert row.panel_security_ids == [str(s) for s in ctx["panel"]]
    assert chain.head_seq == 0
    assert chain.head_hash == ledger_service.GENESIS_HASH


async def test_campaign_idempotent_and_conflict(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id)
    again = await _campaign(db_engine, tenant_id, ctx["benchmark"], ctx["panel"])
    assert not again.created
    assert again.campaign_id == ctx["campaign"].campaign_id

    with pytest.raises(CampaignConflict):
        await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key="c-test-1",
            release_id="rel-test-v1",
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=ctx["benchmark"],
            panel_security_ids=[str(ctx["panel"][0])],  # different panel
            panel_manifest={},
            enabled_sources=["baseline", "quant_model"],
            **_plan_hashes(),
        )


async def test_campaign_rejects_phase1b_sources_and_bad_panels(db_engine, tenant_id):
    await _build_calendar(db_engine)
    benchmark = await _security(db_engine, ticker="SPY")
    panel = [await _security(db_engine, ticker="S0")]
    await _release(db_engine)

    with pytest.raises(CampaignValidationError, match="fallback_policy"):
        await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key="c-bad",
            release_id="rel-test-v1",
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark,
            panel_security_ids=[str(panel[0])],
            panel_manifest={},
            # Phase 1B sources but Phase 1A fallback -> mismatch
            enabled_sources=["baseline", "quant_model", "llm_adjusted"],
            fallback_policy="phase1a-none",
            **_plan_hashes(),
        )

    with pytest.raises(CampaignValidationError, match="unknown security"):
        await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key="c-bad2",
            release_id="rel-test-v1",
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark,
            panel_security_ids=[str(uuid.uuid4())],
            panel_manifest={},
            enabled_sources=["baseline", "quant_model"],
            **_plan_hashes(),
        )

    with pytest.raises(CampaignValidationError, match="missing target specs"):
        await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key="c-bad3",
            release_id="rel-test-v1",
            target_specs=_specs()[:2],
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark,
            panel_security_ids=[str(panel[0])],
            panel_manifest={},
            enabled_sources=["baseline", "quant_model"],
        )


# --- batch planning ------------------------------------------------------------


async def test_plan_batch_seals_known_answer_windows(db_engine, tenant_id):
    """S04b-verified known answer: 2026-09-26 Saturday cutoff ->
    entry 09-28 09:30 EDT, D20 exit 10-23, D60 exit 12-21."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1, first_cutoff=FIRST_CUTOFF_2026Q4)
    cutoff = datetime(2026, 9, 26, 6, 0, tzinfo=ET)
    backfill = datetime.now(UTC) > cutoff.astimezone(UTC)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=cutoff,
        backfilled_plan=backfill,
    )
    assert plan.created
    assert len(plan.case_ids) == 3  # 1 security x 3 horizons

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT horizon_td, decision_cutoff_utc, prediction_deadline_utc, "
                    "entry_at_utc, exit_at_utc, target_spec_id, benchmark_security_id "
                    "FROM forecast_cases WHERE batch_id = :b ORDER BY horizon_td"
                ),
                {"b": str(plan.batch_id)},
            )
        ).mappings().all()
        batch = (
            await conn.execute(
                text("SELECT * FROM forecast_batches WHERE id = :b"),
                {"b": str(plan.batch_id)},
            )
        ).mappings().one()

    cutoff_utc = cutoff.astimezone(UTC)
    assert batch.decision_cutoff_utc == cutoff_utc
    assert batch.decision_cutoff_utc.isoformat() == "2026-09-26T10:00:00+00:00"
    assert batch.entry_date.isoformat() == "2026-09-28"
    assert batch.prediction_deadline_utc.isoformat() == "2026-09-28T13:15:00+00:00"
    assert batch.entry_at_utc.isoformat() == "2026-09-28T13:30:00+00:00"
    assert batch.batch_manifest["calendar_version"].startswith("xnys-")
    assert batch.batch_manifest["tzdb_version"]

    by_horizon = {r.horizon_td: r for r in rows}
    for r in rows:
        assert r.decision_cutoff_utc == cutoff_utc
        assert r.benchmark_security_id == ctx["benchmark"]
        assert r.target_spec_id == f"excess-tr-d{r.horizon_td}-v1"
    # D1: entry day close; D20: 10-23; D60: 12-21 (DST -> EST after Nov 1)
    assert by_horizon[1].exit_at_utc.isoformat() == "2026-09-28T20:00:00+00:00"
    assert by_horizon[20].exit_at_utc.isoformat() == "2026-10-23T20:00:00+00:00"
    assert by_horizon[60].exit_at_utc.isoformat() == "2026-12-21T21:00:00+00:00"


async def test_plan_batch_rejects_non_saturday_cutoff(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    with pytest.raises(CalendarError):
        await plan_batch(
            db_engine,
            ctx["campaign"].campaign_id,
            decision_cutoff=datetime(2026, 9, 25, 6, 0, tzinfo=ET),  # Friday
            backfilled_plan=True,
        )


async def test_plan_batch_future_cutoff_and_idempotency(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=2, first_cutoff=FIRST_CUTOFF_2026Q4)
    cutoff = next_weekly_cutoff(datetime.now(UTC))
    plan = await plan_batch(db_engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff)
    assert plan.created
    assert len(plan.case_ids) == 6  # 2 securities x 3 horizons

    again = await plan_batch(db_engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff)
    assert not again.created
    assert again.batch_id == plan.batch_id
    assert sorted(again.case_ids) == sorted(plan.case_ids)


async def test_plan_batch_past_cutoff_requires_explicit_backfill(db_engine, tenant_id):
    # Anchor the frozen plan three weeks in the past so its list CONTAINS a
    # cutoff that is already behind "now" (two weeks back) — that is the "past
    # cutoff needs explicit backfill" case.
    ctx = await _setup(
        db_engine, tenant_id, n_panel=1,
        first_cutoff=next_weekly_cutoff(datetime.now(UTC) - timedelta(weeks=3)),
    )
    cutoff = next_weekly_cutoff(datetime.now(UTC) - timedelta(weeks=2))
    with pytest.raises(PlanBackfillRequired):
        await plan_batch(db_engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff)

    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff, backfilled_plan=True
    )
    assert plan.created

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT backfilled_plan FROM forecast_batches WHERE id = :b"),
                {"b": str(plan.batch_id)},
            )
        ).mappings().one()
    assert row.backfilled_plan is True

    # the missed week stays in the plan with a recorded reason
    await record_batch_miss(db_engine, plan.batch_id, reason="vendor outage")
    async with db_engine.begin() as conn:
        ev = (
            await conn.execute(
                text(
                    "SELECT payload FROM events WHERE event_type = 'batch.missed' "
                    "AND payload->>'batch_id' = :b"
                ),
                {"b": str(plan.batch_id)},
            )
        ).mappings().one()
    assert ev.payload["reason"] == "vendor outage"


# --- append-only enforcement ----------------------------------------------------


async def test_ledger_tables_reject_update_delete_truncate(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1, first_cutoff=FIRST_CUTOFF_2026Q4)
    cutoff = next_weekly_cutoff(datetime.now(UTC))
    plan = await plan_batch(db_engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff)
    case_id = plan.case_ids[0]

    for sql in (
        f"UPDATE campaigns SET status = 'closed'",
        f"DELETE FROM forecast_cases WHERE id = '{case_id}'",
        # predictions has no inbound FKs, so the trigger is what fires
        "TRUNCATE TABLE predictions",
    ):
        with pytest.raises(Exception, match="append-only"):
            async with db_engine.begin() as conn:
                await conn.execute(text(sql))

    # the ops/test escape hatch allows recovery operations
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(text("UPDATE campaigns SET status = 'closed'"))
    async with db_engine.begin() as conn:
        status = (
            await conn.execute(text("SELECT status FROM campaigns"))
        ).scalar_one()
    assert status == "closed"
