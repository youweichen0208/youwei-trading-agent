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
   resolved heads are not auto-re-run (vendor corrections to already
   resolved outcomes are explicit operations) and unscorable is
   sticky
4. reports: every (batch, horizon) whose cases are all mature and
   definite gets its report regenerated — idempotent no-op when
   nothing changed, a new version when an outcome was corrected

The tick runs on the database clock, the same clock that stamps
sealed_at.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.db.meta import (
    campaigns,
    forecast_batches,
    forecast_cases,
    outcome_revisions,
    research_releases,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
from youwei_core.ledger.evaluation import NotReady, generate_batch_report
from youwei_core.ledger.outcomes import resolve_outcome
from youwei_core.ledger.service import plan_batch, record_batch_miss

PREDICT_JOB_KIND = "research.batch_predict"
HORIZONS = (1, 20, 60)


async def scheduler_tick(engine: AsyncEngine) -> dict:
    summary: dict = {
        "batches_planned": [],
        "missed_backfilled": [],
        "predict_runs_submitted": [],
        "outcomes_attempted": 0,
        "reports_generated": [],
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
            continue  # resolved: corrections are explicit; unscorable: sticky
        try:
            await resolve_outcome(engine, case_id)
            summary["outcomes_attempted"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                {"case_id": str(case_id), "phase": "outcome", "error": str(exc)[:300]}
            )

    # --- 4. report regeneration --------------------------------------------
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
    for batch_id in batch_ids:
        for horizon in HORIZONS:
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

    return summary
