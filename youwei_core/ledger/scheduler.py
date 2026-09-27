"""Scheduler tick (S06): the deterministic Controller-side clock.

One idempotent pass, safe to run repeatedly (and safe to re-run after
a crash — every step is an append-only idempotent operation):

1. batch planning: every active campaign always has its coming
   Saturday-06:00-ET cutoff pre-registered BEFORE the cutoff; weeks
   the scheduler missed are backfilled as explicit missed records
   (they stay in the coverage denominator, never silently skipped)
2. prediction runs: a batch inside its window (cutoff passed,
   deadline not yet) gets exactly one `research.batch_predict` run
   (idempotency key per batch)
3. outcomes: cases past their planned exit get resolved; unresolved
   heads are re-attempted (late data arrives as new revisions);
   resolved heads are re-resolved only when the data underneath them
   actually changed (vendor corrections append new revisions — a cheap
   candidate scan plus an exact evidence comparison, so unchanged
   cases are never re-frozen) and unscorable is sticky
4. reports: every (batch, horizon) whose cases are all mature and
   definite gets its report regenerated — idempotent no-op when
   nothing changed, a new version when an outcome was corrected; an
   inputs digest per batch skips regenerations whose inputs are
   unchanged
5. monthly summaries: every ended month with batches (due on the
   first regular trading day of the next month at 06:00 ET,
   campaign-policy §4.2) gets its batch-equal-weighted summary
   regenerated — versions append as labels mature or get corrected

The tick runs on the database clock, the same clock that stamps
sealed_at.
"""

from sqlalchemy import Date, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.db.meta import (
    batch_report_input_state,
    campaigns,
    evaluation_reports,
    forecast_batches,
    forecast_cases,
    monthly_report_input_state,
    monthly_summary_reports,
    outcome_revisions,
    price_observations,
    raw_objects,
    research_releases,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
from youwei_core.ledger.evaluation import (
    NotReady,
    batch_input_digests,
    generate_batch_report,
)
from youwei_core.ledger.monthly import (
    generate_monthly_report,
    load_month_state,
    monthly_input_digest,
)
from youwei_core.ledger.outcomes import outcome_evidence_changed, resolve_outcome
from youwei_core.ledger.service import plan_batch, record_batch_miss

PREDICT_JOB_KIND = "research.batch_predict"
HORIZONS = (1, 20, 60)


async def _resolved_cases_with_new_window_data(engine: AsyncEngine, db_now) -> list:
    """Candidate resolved cases whose holding-window data may have
    changed since their head was recorded: a price observation for
    either security inside [entry, exit] from a raw object ingested
    — or made usable — after the head's recorded_at, and already
    usable now. A cheap necessary condition only; the exact evidence
    comparison (outcome_evidence_changed) runs before any
    re-resolution, so a false positive costs one PIT query, never a
    re-freeze."""
    head = (
        select(
            outcome_revisions.c.case_id,
            outcome_revisions.c.status,
            outcome_revisions.c.recorded_at,
        )
        .prefix_with("DISTINCT ON (case_id)", dialect="postgresql")
        .order_by(outcome_revisions.c.case_id, outcome_revisions.c.revision.desc())
        .subquery()
    )
    new_obs = exists(
        select(1)
        .select_from(
            price_observations.join(
                raw_objects, raw_objects.c.id == price_observations.c.raw_object_id
            )
        )
        .where(
            or_(
                price_observations.c.security_id == forecast_cases.c.security_id,
                price_observations.c.security_id
                == forecast_cases.c.benchmark_security_id,
            ),
            price_observations.c.trade_date >= cast(forecast_cases.c.entry_at_utc, Date),
            price_observations.c.trade_date <= cast(forecast_cases.c.exit_at_utc, Date),
            or_(
                raw_objects.c.ingested_at > head.c.recorded_at,
                raw_objects.c.usable_at > head.c.recorded_at,
            ),
            raw_objects.c.usable_at <= db_now,
        )
    )
    async with engine.begin() as conn:
        return (
            (
                await conn.execute(
                    select(forecast_cases.c.id)
                    .select_from(forecast_cases)
                    .join(
                        forecast_batches,
                        forecast_batches.c.id == forecast_cases.c.batch_id,
                    )
                    .join(campaigns, campaigns.c.id == forecast_cases.c.campaign_id)
                    .join(head, head.c.case_id == forecast_cases.c.id)
                    .where(
                        campaigns.c.status == "active",
                        forecast_cases.c.exit_at_utc <= db_now,
                        head.c.status == "resolved",
                        new_obs,
                    )
                )
            )
            .scalars()
            .all()
        )


async def scheduler_tick(engine: AsyncEngine) -> dict:
    summary: dict = {
        "batches_planned": [],
        "missed_backfilled": [],
        "predict_runs_submitted": [],
        "outcomes_attempted": 0,
        "outcomes_corrected": 0,
        "reports_generated": [],
        "reports_skipped": 0,
        "monthly_reports_generated": [],
        "monthly_reports_skipped": 0,
        "errors": [],
    }
    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()
        active = (
            (
                await conn.execute(
                    select(campaigns).where(campaigns.c.status == "active")
                )
            )
            .mappings()
            .all()
        )

    # --- 1. batch planning -------------------------------------------------
    for campaign in active:
        try:
            async with engine.begin() as conn:
                last = (
                    await conn.execute(
                        select(func.max(forecast_batches.c.decision_cutoff_utc)).where(
                            forecast_batches.c.campaign_id == campaign.id
                        )
                    )
                ).scalar_one_or_none()
            if last is None:
                # first batch: the next cutoff eligible at/after now;
                # no looking back before the campaign existed
                # (time-protocol §2)
                cutoffs = [next_weekly_cutoff(db_now)]
            else:
                cutoffs = []
                target = next_weekly_cutoff(db_now)
                candidate = next_weekly_cutoff(last)
                while candidate <= target:
                    cutoffs.append(candidate)
                    candidate = next_weekly_cutoff(candidate)
            for cutoff in cutoffs:
                backfilled = cutoff <= db_now
                plan = await plan_batch(
                    engine,
                    campaign.id,
                    decision_cutoff=cutoff,
                    backfilled_plan=backfilled,
                )
                if plan.created:
                    summary["batches_planned"].append(str(plan.batch_id))
                    if backfilled:
                        summary["missed_backfilled"].append(str(plan.batch_id))
                        await record_batch_miss(
                            engine, plan.batch_id, reason="scheduler_backfill"
                        )
        except Exception as exc:  # noqa: BLE001 — one campaign must not stop the tick
            summary["errors"].append(
                {"campaign_id": str(campaign.id), "phase": "planning", "error": str(exc)[:300]}
            )

    # --- 2. prediction runs inside their window ----------------------------
    async with engine.begin() as conn:
        windows = (
            (
                await conn.execute(
                    select(
                        forecast_batches.c.id,
                        forecast_batches.c.campaign_id,
                        research_releases.c.release_id,
                        campaigns.c.tenant_id,
                    )
                    .select_from(forecast_batches)
                    .join(campaigns, campaigns.c.id == forecast_batches.c.campaign_id)
                    .join(
                        research_releases,
                        research_releases.c.id == campaigns.c.release_row_id,
                    )
                    .where(
                        campaigns.c.status == "active",
                        forecast_batches.c.decision_cutoff_utc <= db_now,
                        forecast_batches.c.prediction_deadline_utc > db_now,
                    )
                )
            )
            .mappings()
            .all()
        )
    for row in windows:
        try:
            result = await submit_run(
                engine,
                row.tenant_id,
                RunSubmission(
                    kind=PREDICT_JOB_KIND,
                    total_budget_micros=0,
                    jobs=[
                        JobSubmission(
                            kind=PREDICT_JOB_KIND,
                            payload={
                                "batch_id": str(row.id),
                                "release_id": row.release_id,
                            },
                        )
                    ],
                ),
                idempotency_key=f"batch-predict:{row.id}",
            )
            if result.created:
                summary["predict_runs_submitted"].append(str(result.run_id))
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"batch_id": str(row.id), "phase": "submit", "error": str(exc)[:300]}
            )

    # --- 3. outcome resolution for matured cases ---------------------------
    async with engine.begin() as conn:
        due = (
            (
                await conn.execute(
                    select(forecast_cases.c.id)
                    .select_from(forecast_cases)
                    .join(
                        forecast_batches,
                        forecast_batches.c.id == forecast_cases.c.batch_id,
                    )
                    .join(campaigns, campaigns.c.id == forecast_cases.c.campaign_id)
                    .where(
                        campaigns.c.status == "active",
                        forecast_cases.c.exit_at_utc <= db_now,
                    )
                )
            )
            .scalars()
            .all()
        )
        heads = {}
        if due:
            rows = (
                (
                    await conn.execute(
                        select(
                            outcome_revisions.c.case_id,
                            outcome_revisions.c.status,
                        )
                        .where(outcome_revisions.c.case_id.in_(due))
                        .prefix_with("DISTINCT ON (case_id)", dialect="postgresql")
                        .order_by(
                            outcome_revisions.c.case_id,
                            outcome_revisions.c.revision.desc(),
                        )
                    )
                )
                .mappings()
                .all()
            )
            heads = {r.case_id: r.status for r in rows}
    for case_id in due:
        status = heads.get(case_id)
        if status in ("resolved", "unscorable"):
            continue  # resolved: see the correction scan below; unscorable: sticky
        try:
            await resolve_outcome(engine, case_id)
            summary["outcomes_attempted"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"case_id": str(case_id), "phase": "outcome", "error": str(exc)[:300]}
            )

    # --- 3b. vendor data corrections under resolved outcomes ---------------
    # resolved heads are stable, but the vendor data underneath them is
    # not: when a correction or late version lands inside a case's
    # window, re-resolve it. Appends a new revision superseding the head
    # (correction_reason defaults to data_revision); genuinely unchanged
    # evidence re-freezes identical content and appends nothing.
    try:
        candidates = await _resolved_cases_with_new_window_data(engine, db_now)
    except Exception as exc:  # noqa: BLE001
        candidates = []
        summary["errors"].append(
            {"phase": "correction_scan", "error": str(exc)[:300]}
        )
    for case_id in candidates:
        try:
            if await outcome_evidence_changed(engine, case_id):
                attempt = await resolve_outcome(engine, case_id)
                if attempt.created:
                    summary["outcomes_corrected"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"case_id": str(case_id), "phase": "correction", "error": str(exc)[:300]}
            )

    # --- 4. report regeneration (digest-gated) ------------------------------
    # a batch report's content is a pure function of its inputs (cases,
    # outcome heads, commits, confirmations, maturity). The tick hashes
    # those per batch and skips (batch, horizon) pairs whose inputs are
    # unchanged since the last generation — history no longer gets
    # fully re-hashed every tick. A NotReady attempt also records the
    # digest: the input change that unblocks it (a head appearing, an
    # exit passing) re-triggers generation on a later tick.
    async with engine.begin() as conn:
        batch_ids = (
            (
                await conn.execute(
                    select(forecast_batches.c.id)
                    .join(campaigns, campaigns.c.id == forecast_batches.c.campaign_id)
                    .where(campaigns.c.status == "active")
                )
            )
            .scalars()
            .all()
        )
    digests = await batch_input_digests(engine, batch_ids, db_now)
    async with engine.begin() as conn:
        state_rows = (
            (
                await conn.execute(
                    select(
                        batch_report_input_state.c.batch_id,
                        batch_report_input_state.c.inputs_sha256,
                    ).where(
                        batch_report_input_state.c.batch_id.in_(batch_ids)
                    )
                )
            )
            .mappings()
            .all()
        )
        gate_by_batch = {r.batch_id: r.inputs_sha256 for r in state_rows}
        existing = {
            (r.batch_id, r.horizon_td)
            for r in (
                (
                    await conn.execute(
                        select(
                            evaluation_reports.c.batch_id,
                            evaluation_reports.c.horizon_td,
                        ).where(evaluation_reports.c.batch_id.in_(batch_ids))
                    )
                )
                .mappings()
                .all()
            )
        }
    for batch_id in batch_ids:
        digest = digests.get(batch_id)
        if digest is None:
            continue
        state_matches = gate_by_batch.get(batch_id) == digest
        for horizon in HORIZONS:
            if state_matches and (batch_id, horizon) in existing:
                summary["reports_skipped"] += 1
                continue
            try:
                report = await generate_batch_report(engine, batch_id, horizon)
                if report.created:
                    summary["reports_generated"].append(
                        {"batch_id": str(batch_id), "horizon_td": horizon}
                    )
            except NotReady:
                continue
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    {
                        "batch_id": str(batch_id),
                        "phase": "report",
                        "error": str(exc)[:300],
                    }
                )
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    pg_insert(batch_report_input_state)
                    .values(batch_id=batch_id, inputs_sha256=digest)
                    .on_conflict_do_update(
                        index_elements=[batch_report_input_state.c.batch_id],
                        set_={
                            "inputs_sha256": digest,
                            "updated_at": func.now(),
                        },
                    )
                )
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"batch_id": str(batch_id), "phase": "report_gate", "error": str(exc)[:300]}
            )

    # --- 5. monthly summaries (campaign-policy §4.2) -------------------------
    # every ENDED month holding batches, once its scheduled generation
    # moment has passed (first regular trading day of the next month at
    # 06:00 ET), gets its batch-equal-weighted summary; versions append
    # as labels mature or get corrected. Digest-gated like the batch
    # reports, with the maturity vector inside the digest.
    for campaign in active:
        try:
            async with engine.begin() as conn:
                cutoffs = (
                    (
                        await conn.execute(
                            select(forecast_batches.c.decision_cutoff_utc).where(
                                forecast_batches.c.campaign_id == campaign.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            months = sorted({c.date().replace(day=1) for c in cutoffs})
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"campaign_id": str(campaign.id), "phase": "monthly", "error": str(exc)[:300]}
            )
            continue
        for month in months:
            try:
                state = await load_month_state(engine, campaign.id, month, db_now)
                if state is None:
                    continue
                if db_now < state.due_at:
                    continue  # not due yet: the registered schedule
                digest = monthly_input_digest(state)
                async with engine.begin() as conn:
                    gate = (
                        await conn.execute(
                            select(monthly_report_input_state.c.inputs_sha256).where(
                                monthly_report_input_state.c.campaign_id == campaign.id,
                                monthly_report_input_state.c.month == month,
                            )
                        )
                    ).scalar_one_or_none()
                    has_report = (
                        await conn.execute(
                            select(monthly_summary_reports.c.id)
                            .where(
                                monthly_summary_reports.c.campaign_id == campaign.id,
                                monthly_summary_reports.c.month == month,
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if gate == digest and has_report is not None:
                    summary["monthly_reports_skipped"] += 1
                    continue
                result = await generate_monthly_report(engine, campaign.id, month)
                if result.created:
                    summary["monthly_reports_generated"].append(
                        {
                            "month": month.isoformat(),
                            "report_version": result.report_version,
                        }
                    )
                async with engine.begin() as conn:
                    await conn.execute(
                        pg_insert(monthly_report_input_state)
                        .values(
                            campaign_id=campaign.id,
                            month=month,
                            inputs_sha256=result.inputs_sha256,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                monthly_report_input_state.c.campaign_id,
                                monthly_report_input_state.c.month,
                            ],
                            set_={
                                "inputs_sha256": result.inputs_sha256,
                                "updated_at": func.now(),
                            },
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    {
                        "campaign_id": str(campaign.id),
                        "month": month.isoformat(),
                        "phase": "monthly",
                        "error": str(exc)[:300],
                    }
                )

    return summary
