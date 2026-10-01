"""S06a: forward prediction pipeline — scheduler, batch predict jobs,
status visibility.

Acceptance mapped from the plan (S06):
- the scheduler pre-registers each active campaign's coming cutoff
  BEFORE it passes; missed weeks are backfilled as explicit missed
  records that stay in the denominator
- a batch inside its window gets exactly one prediction run
  (idempotent by batch)
- the predict job freezes ONE evidence snapshot at the batch cutoff
  and seals every case's three positions (Phase 1A llm fixed at
  unavailable/not_enabled); per-case failures are reported, and job
  retries are idempotent
- the tick resolves matured outcomes and generates complete reports
- campaign status shows planned/committed/timeliness/outcome/report
  counts, tenant-scoped at the API
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
from youwei_core.jobs.worker import claim_next_job, complete_attempt, fail_attempt
from youwei_core.ledger.evaluation import latest_report
from youwei_core.ledger.outcomes import current_outcome, record_unscorable
from youwei_core.ledger.pipeline import make_batch_predict_handler
from youwei_core.ledger.scheduler import scheduler_tick
from youwei_core.ledger.service import plan_batch
from youwei_core.ledger.status import campaign_status
from test_data_pit import asyncio_sleep
from test_ledger_campaign import _setup
from test_ledger_outcomes import _ingest_window, _retarget_case, _window_dates
from test_ledger_seal import _shift_case_window


async def _open_batch(engine, batch_id, case_ids, *, cutoff=None, deadline=None):
    """Move a batch and its cases into an open sealing window (test
    fixture via the ops escape hatch). Default: cutoff=now (data
    ingested before now stays PIT-visible), deadline=now+1h."""
    async with engine.begin() as conn:
        db_now = (await conn.execute(text("SELECT now()"))).scalar_one()
        c = cutoff or db_now
        d = deadline or db_now + timedelta(hours=1)
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE forecast_batches SET decision_cutoff_utc = :c, "
                "prediction_deadline_utc = :d WHERE id = :b"
            ),
            {"c": c, "d": d, "b": str(batch_id)},
        )
        for cid in case_ids:
            await conn.execute(
                text(
                    "UPDATE forecast_cases SET decision_cutoff_utc = :c, "
                    "prediction_deadline_utc = :d WHERE id = :id"
                ),
                {"c": c, "d": d, "id": str(cid)},
            )


async def _run_predict_job(engine, tenant_id, batch_id, release_id="rel-test-v1"):
    """Submit + claim + execute the batch predict handler directly."""
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(
            kind="research.batch_predict",
            jobs=[
                JobSubmission(
                    kind="research.batch_predict",
                    payload={"batch_id": str(batch_id), "release_id": release_id},
                )
            ],
        ),
        idempotency_key=f"batch-predict:{batch_id}",
    )
    claimed = await claim_next_job(engine, "test-worker")
    assert claimed is not None and claimed.kind == "research.batch_predict"
    handler = make_batch_predict_handler(engine)
    summary = await handler(claimed)
    await complete_attempt(engine, claimed.job_id, claimed.attempt_no, summary)
    return summary, claimed


# --- scheduler: planning -------------------------------------------------------


async def test_tick_preregisters_coming_cutoff(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    summary = await scheduler_tick(db_engine)
    assert summary["batches_planned"] and not summary["missed_backfilled"]

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT decision_cutoff_utc, backfilled_plan FROM forecast_batches "
                    "WHERE campaign_id = :c"
                ),
                {"c": str(ctx["campaign"].campaign_id)},
            )
        ).mappings().all()
    assert len(rows) == 1
    assert rows[0].decision_cutoff_utc == next_weekly_cutoff(datetime.now(UTC))
    assert rows[0].backfilled_plan is False  # registered BEFORE the cutoff

    # idempotent: nothing new on the next tick
    summary2 = await scheduler_tick(db_engine)
    assert not summary2["batches_planned"]


async def test_tick_backfills_missed_weeks(db_engine, tenant_id):
    # Anchor the frozen plan exactly at the three-weeks-ago cutoff this test
    # backfills, so the plan contains it plus the two gap weeks and the coming
    # Saturday — and nothing earlier (a 4-week anchor would add a third gap).
    ctx = await _setup(
        db_engine, tenant_id, n_panel=1,
        first_cutoff=next_weekly_cutoff(datetime.now(UTC) - timedelta(weeks=3)),
    )
    # a batch three Saturdays ago (an outage gap after it)
    past = next_weekly_cutoff(datetime.now(UTC) - timedelta(weeks=3))
    await plan_batch(
        db_engine, ctx["campaign"].campaign_id, decision_cutoff=past, backfilled_plan=True
    )

    summary = await scheduler_tick(db_engine)
    # two missed weeks backfilled + the coming Saturday pre-registered
    assert len(summary["missed_backfilled"]) == 2
    assert len(summary["batches_planned"]) == 3

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT decision_cutoff_utc, backfilled_plan FROM forecast_batches "
                    "WHERE campaign_id = :c ORDER BY decision_cutoff_utc"
                ),
                {"c": str(ctx["campaign"].campaign_id)},
            )
        ).mappings().all()
        misses = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM events WHERE event_type = 'batch.missed' "
                    "AND payload->>'reason' = 'scheduler_backfill'"
                )
            )
        ).scalar_one()
    assert len(rows) == 4
    assert [r.backfilled_plan for r in rows] == [True, True, True, False]
    assert rows[-1].decision_cutoff_utc > datetime.now(UTC)
    assert misses == 2  # the two tick backfills carry explicit miss records


# --- scheduler: prediction submission -------------------------------------------


async def test_tick_submits_one_predict_run_per_window(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # open the window: cutoff passed, deadline not yet
    await _open_batch(db_engine, plan.batch_id, plan.case_ids)

    summary = await scheduler_tick(db_engine)
    assert len(summary["predict_runs_submitted"]) == 1

    # idempotent: the same batch never gets a second run
    summary2 = await scheduler_tick(db_engine)
    assert not summary2["predict_runs_submitted"]

    async with db_engine.begin() as conn:
        runs = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM runs WHERE kind = 'research.batch_predict'"
                )
            )
        ).scalar_one()
    assert runs == 1


# --- the predict handler ---------------------------------------------------------


async def test_batch_predict_seals_all_cases_with_shared_evidence(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=2)
    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # evidence bars: 25 trading days, flat closes (momentum 0)
    _, _, dates = await _window_dates(db_engine, exit_sessions_back=1, span=25)
    for i in range(2):
        await _ingest_window(db_engine, f"S{i}", ctx["panel"][i], dates)
    await _ingest_window(db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})
    await _open_batch(db_engine, plan.batch_id, plan.case_ids)

    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 6  # 2 securities x 3 horizons
    assert summary["failed"] == []

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT p.source, p.source_status, p.p_outperform, "
                    "p.evidence_snapshot_id, p.model_version, c.chain_seq "
                    "FROM predictions p JOIN forecast_commits c ON c.id = p.commit_id "
                    "ORDER BY c.chain_seq, p.source"
                )
            )
        ).mappings().all()
    assert len(rows) == 18
    quant_snapshots = {r.evidence_snapshot_id for r in rows if r.source == "quant_model"}
    assert len(quant_snapshots) == 1  # every quant position references the batch's ONE shared snapshot
    assert all(
        r.evidence_snapshot_id is None for r in rows if r.source == "baseline"
    )  # the constant model consumed no evidence — no decorative reference
    assert all(
        r.evidence_snapshot_id is None for r in rows if r.source == "llm_adjusted"
    )  # unavailable/not_enabled consumed nothing
    by_source = {}
    for r in rows:
        by_source.setdefault(r.source, []).append(r)
    assert all(r.source_status == "produced" for r in by_source["baseline"])
    assert all(r.p_outperform == 0.5 for r in by_source["baseline"])
    assert all(r.source_status == "produced" for r in by_source["quant_model"])
    assert all(r.p_outperform == 0.5 for r in by_source["quant_model"])  # flat -> momentum 0
    assert all(r.source_status == "unavailable" for r in by_source["llm_adjusted"])
    assert all(r.model_version == "quant-momentum-v0" for r in by_source["quant_model"])

    # job retry with a fresh attempt is idempotent: nothing new sealed
    await submit_run(
        db_engine,
        tenant_id,
        RunSubmission(
            kind="research.batch_predict",
            jobs=[{
                "kind": "research.batch_predict",
                "payload": {"batch_id": str(plan.batch_id), "release_id": "rel-test-v1"},
                "max_attempts": 2,
            }],
        ),
        idempotency_key=f"retry-{uuid.uuid4()}",
    )
    first = await claim_next_job(db_engine, "test-worker")
    await fail_attempt(db_engine, first.job_id, first.attempt_no, "retry")
    second = await claim_next_job(db_engine, "test-worker")
    handler = make_batch_predict_handler(db_engine)
    summary2 = await handler(second)
    assert summary2["sealed"] == 0
    assert summary2["already_sealed"] == 6


async def test_batch_predict_quant_unavailable_without_history(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # NO bars at all: the evidence snapshot records the absence
    await _open_batch(db_engine, plan.batch_id, plan.case_ids)

    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 3

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT source, source_status, reason, evidence_snapshot_id "
                    "FROM predictions WHERE source IN ('quant_model', 'llm_adjusted')"
                )
            )
        ).mappings().all()
    by_source = {r.source: r for r in rows}
    assert by_source["quant_model"].source_status == "unavailable"
    assert by_source["quant_model"].reason == "insufficient_history"
    assert (
        by_source["quant_model"].evidence_snapshot_id is not None
    )  # the snapshot records the ABSENCE that made the model unable to run
    assert by_source["llm_adjusted"].reason == "not_enabled"


async def test_batch_predict_reports_per_case_failures(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    await _open_batch(db_engine, plan.batch_id, plan.case_ids)
    # one case's deadline is already past: it cannot be sealed
    await _shift_case_window(
        db_engine, plan.case_ids[2], (timedelta(hours=-2), timedelta(hours=-1))
    )

    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 2
    assert len(summary["failed"]) == 1
    assert "deadline" in summary["failed"][0]["error"]


# --- outcome + report ticks --------------------------------------------------------


async def test_tick_resolves_outcomes_and_generates_reports(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await scheduler_tick(db_engine)  # plans the coming batch
    async with db_engine.begin() as conn:
        batch_id = (
            await conn.execute(
                text("SELECT id FROM forecast_batches WHERE campaign_id = :c"),
                {"c": str(ctx["campaign"].campaign_id)},
            )
        ).scalar_one()
        case_ids = (
            await conn.execute(
                text("SELECT id FROM forecast_cases WHERE batch_id = :b ORDER BY horizon_td"),
                {"b": str(batch_id)},
            )
        ).scalars().all()

    # evidence + outcome bars (35 flat days: covers the 25-day lookback
    # AND the retargeted D20 window ending 10 sessions back) and an open
    # sealing window
    _, _, dates = await _window_dates(db_engine, exit_sessions_back=1, span=35)
    await _ingest_window(db_engine, "S0", ctx["panel"][0], dates)
    await _ingest_window(db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})
    await _open_batch(db_engine, batch_id, case_ids)
    summary, _ = await _run_predict_job(db_engine, tenant_id, batch_id)
    assert summary["sealed"] == 3

    # mature the D20 case: same bars cover its window
    entry_date, exit_date, _ = await _window_dates(
        db_engine, exit_sessions_back=10, span=20
    )
    await _retarget_case(db_engine, case_ids[1], entry_date, exit_date)

    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_attempted"] >= 1
    head = await current_outcome(db_engine, case_ids[1])
    assert head["status"] == "resolved"

    # D20 complete (1 case, matured, headed) -> report; D1/D60 immature
    assert any(r["horizon_td"] == 20 for r in tick["reports_generated"])
    assert all(r["horizon_td"] != 1 for r in tick["reports_generated"])

    # status view reflects the whole pipeline. Note: the fixture's
    # shifted cutoff (a non-Saturday instant) makes the tick
    # pre-register the coming Saturday again — production batches
    # keep immutable Saturday cutoffs, so that second batch is a test
    # artifact; assert on the batch we actually drove.
    view = await campaign_status(db_engine, ctx["campaign"].campaign_id)
    orig = next(b for b in view["batches"] if b["batch_id"] == str(batch_id))
    assert orig["planned_cases"] == 3
    assert orig["with_commit"] == 3
    assert orig["on_time"] == 3
    assert orig["outcome_resolved"] == 1
    assert orig["outcome_pending"] == 2
    assert orig["reports"] == {"20": 1}
    assert view["totals"]["batches"] == 2
    empty = next(b for b in view["batches"] if b["batch_id"] != str(batch_id))
    assert empty["no_commit"] == 3  # planned but never run: visible, not hidden


# --- vendor corrections to resolved outcomes ------------------------------------


async def _drive_resolved_d20(engine, tenant_id):
    """A planned batch with sealed predictions whose D20 case is
    retargeted to a past window and resolved by a tick."""
    ctx = await _setup(engine, tenant_id, n_panel=1)
    await scheduler_tick(engine)  # plans the coming batch
    async with engine.begin() as conn:
        batch_id = (
            await conn.execute(
                text("SELECT id FROM forecast_batches WHERE campaign_id = :c"),
                {"c": str(ctx["campaign"].campaign_id)},
            )
        ).scalar_one()
        case_ids = (
            await conn.execute(
                text("SELECT id FROM forecast_cases WHERE batch_id = :b ORDER BY horizon_td"),
                {"b": str(batch_id)},
            )
        ).scalars().all()

    _, _, dates = await _window_dates(engine, exit_sessions_back=1, span=35)
    await _ingest_window(engine, "S0", ctx["panel"][0], dates)
    await _ingest_window(
        engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    await _open_batch(engine, batch_id, case_ids)
    summary, _ = await _run_predict_job(engine, tenant_id, batch_id)
    assert summary["sealed"] == 3

    entry_date, exit_date, _ = await _window_dates(engine, exit_sessions_back=10, span=20)
    await _retarget_case(engine, case_ids[1], entry_date, exit_date)
    tick = await scheduler_tick(engine)
    assert tick["outcomes_attempted"] >= 1
    head = await current_outcome(engine, case_ids[1])
    assert head["status"] == "resolved" and head["revision"] == 1
    return ctx, batch_id, case_ids, dates, exit_date


async def test_tick_revises_resolved_outcome_on_vendor_correction(db_engine, tenant_id):
    ctx, batch_id, case_ids, dates, _ = await _drive_resolved_d20(db_engine, tenant_id)
    head1 = await current_outcome(db_engine, case_ids[1])
    assert head1["excess_return"] == "0.0000000000"  # both legs flat

    # a vendor correction: the same window re-published with different closes
    await asyncio_sleep()
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 101.0}
    )

    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_corrected"] == 1
    head2 = await current_outcome(db_engine, case_ids[1])
    assert head2["revision"] == 2
    assert head2["status"] == "resolved"
    assert head2["correction_reason"] == "data_revision"
    assert head2["excess_return"] != head1["excess_return"]  # re-computed

    # the batch report follows the corrected head as a new version
    report = await latest_report(db_engine, batch_id, 20)
    assert report["report_version"] == 2
    assert report["content"]["cases"][0]["outcome"]["revision"] == 2


async def test_tick_leaves_resolved_outcome_alone_without_new_data(db_engine, tenant_id):
    ctx, batch_id, case_ids, dates, _ = await _drive_resolved_d20(db_engine, tenant_id)
    async with db_engine.begin() as conn:
        snaps_before = (
            await conn.execute(text("SELECT count(*) FROM snapshots"))
        ).scalar_one()

    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_corrected"] == 0
    head = await current_outcome(db_engine, case_ids[1])
    assert head["revision"] == 1  # no revision, ...

    async with db_engine.begin() as conn:
        snaps_after = (
            await conn.execute(text("SELECT count(*) FROM snapshots"))
        ).scalar_one()
    assert snaps_after == snaps_before  # ... and no re-freeze without a data change


async def test_tick_unscorable_stays_sticky_under_data_change(db_engine, tenant_id):
    ctx, batch_id, case_ids, dates, _ = await _drive_resolved_d20(db_engine, tenant_id)
    # the D1 case, retargeted to a past window, is recorded unscorable
    entry_date, exit_date, _ = await _window_dates(db_engine, exit_sessions_back=5, span=5)
    await _retarget_case(db_engine, case_ids[0], entry_date, exit_date)
    await record_unscorable(
        db_engine, case_ids[0],
        reason="no_valid_entry_price", evidence_ref="halt-notice",
    )

    # a correction lands inside BOTH windows
    await asyncio_sleep()
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 102.0}
    )

    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_corrected"] == 1  # the resolved D20 case is revised
    head_d1 = await current_outcome(db_engine, case_ids[0])
    assert head_d1["status"] == "unscorable"
    assert head_d1["revision"] == 1  # the market fact stays sticky


async def test_tick_ignores_new_data_outside_the_window(db_engine, tenant_id):
    ctx, batch_id, case_ids, dates, exit_date = await _drive_resolved_d20(db_engine, tenant_id)
    # new bars AFTER the D20 exit are not part of its evidence
    after = [d for d in dates if d > exit_date]
    assert after
    await asyncio_sleep()
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], after, default={"open": 100.0, "close": 150.0}
    )

    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_corrected"] == 0
    head = await current_outcome(db_engine, case_ids[1])
    assert head["revision"] == 1


async def test_tick_skips_unchanged_report_rehashes(db_engine, tenant_id):
    """The tick's report step is digest-gated: history is not fully
    re-hashed every tick; an input change re-triggers exactly once."""
    ctx, batch_id, case_ids, dates, _ = await _drive_resolved_d20(db_engine, tenant_id)

    tick = await scheduler_tick(db_engine)
    assert tick["reports_skipped"] >= 1  # the unchanged D20 report
    assert not tick["reports_generated"]
    report1 = await latest_report(db_engine, batch_id, 20)
    assert report1["report_version"] == 1

    # a correction changes the inputs -> one regeneration, then gated again
    await asyncio_sleep()
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 101.0}
    )
    tick2 = await scheduler_tick(db_engine)
    assert tick2["outcomes_corrected"] == 1
    assert any(
        r["batch_id"] == str(batch_id) and r["horizon_td"] == 20
        for r in tick2["reports_generated"]
    )
    report2 = await latest_report(db_engine, batch_id, 20)
    assert report2["report_version"] == 2

    tick3 = await scheduler_tick(db_engine)
    assert tick3["reports_skipped"] >= 1
    assert not tick3["reports_generated"]
    report3 = await latest_report(db_engine, batch_id, 20)
    assert report3["report_version"] == 2  # no further versions


# --- API ----------------------------------------------------------------------------


async def test_campaign_status_api_tenant_scoped(db_engine, client):
    from youwei_core.auth.service import create_api_key, create_tenant

    tenant = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{tenant}", tenant_id=tenant)
    _, raw = await create_api_key(db_engine, tenant, "test")
    headers = {"Authorization": f"Bearer {raw}"}

    ctx = await _setup(db_engine, tenant, n_panel=1)
    await scheduler_tick(db_engine)

    resp = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}/status", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == str(tenant)
    assert body["totals"]["batches"] == 1
    assert body["totals"]["planned_cases"] == 3

    # another tenant's key must not learn the campaign exists
    other = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{other}", tenant_id=other)
    _, other_raw = await create_api_key(db_engine, other, "test-other")
    resp2 = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}/status",
        headers={"Authorization": f"Bearer {other_raw}"},
    )
    assert resp2.status_code == 404

    # unauthenticated
    resp3 = await client.get(f"/v1/campaigns/{ctx['campaign'].campaign_id}/status")
    assert resp3.status_code == 401
