"""Read-only campaign status (S06): the minimal visibility layer.

Shows, per campaign and per batch: planned cases, commits and their
timeliness, outcome head states, and available report versions —
the planned/completed/missing/late/data-quality/scoring-scope view
the S06 checklist asks for. Pure reads; no state changes.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import (
    campaigns,
    evaluation_reports,
    forecast_batches,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    outcome_revisions,
    research_releases,
)


async def campaign_status(engine: AsyncEngine, campaign_id: uuid.UUID) -> dict | None:
    """Campaign-level status view. None when the campaign does not
    exist. Timeliness here is the raw confirmation state (on_time /
    late / unconfirmed); the evaluation report derives the
    conservative uncertain judgment."""
    async with engine.begin() as conn:
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == campaign_id)
            )
        ).mappings().one_or_none()
        if campaign is None:
            return None
        release = (
            await conn.execute(
                select(research_releases.c.release_id).where(
                    research_releases.c.id == campaign.release_row_id
                )
            )
        ).scalar_one()
        batches = (
            (
                await conn.execute(
                    select(forecast_batches)
                    .where(forecast_batches.c.campaign_id == campaign_id)
                    .order_by(forecast_batches.c.decision_cutoff_utc)
                )
            )
            .mappings()
            .all()
        )
        cases = (
            (
                await conn.execute(
                    select(forecast_cases)
                    .where(forecast_cases.c.campaign_id == campaign_id)
                )
            )
            .mappings()
            .all()
        )

        commits = {}
        if cases:
            rows = (
                (
                    await conn.execute(
                        select(forecast_commits)
                        .where(
                            forecast_commits.c.case_id.in_([c.id for c in cases]),
                            forecast_commits.c.release_row_id == campaign.release_row_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            commits = {cm.case_id: cm for cm in rows}

        confirmations = {}
        if commits:
            rows = (
                (
                    await conn.execute(
                        select(
                            forecast_commit_events.c.commit_id,
                            forecast_commit_events.c.payload,
                        )
                        .where(
                            forecast_commit_events.c.commit_id.in_(
                                [cm.id for cm in commits.values()]
                            ),
                            forecast_commit_events.c.event_type == "durable_confirmation",
                        )
                        .order_by(forecast_commit_events.c.occurred_at.desc())
                    )
                )
                .mappings()
                .all()
            )
            confirmations = {r.commit_id: r.payload for r in rows}

        heads = {}
        if cases:
            rows = (
                (
                    await conn.execute(
                        select(outcome_revisions)
                        .where(outcome_revisions.c.case_id.in_([c.id for c in cases]))
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
            heads = {h.case_id: h for h in rows}

        reports = (
            (
                await conn.execute(
                    select(
                        evaluation_reports.c.batch_id,
                        evaluation_reports.c.horizon_td,
                        evaluation_reports.c.report_version,
                    )
                    .where(evaluation_reports.c.campaign_id == campaign_id)
                    .order_by(
                        evaluation_reports.c.batch_id,
                        evaluation_reports.c.horizon_td,
                    )
                )
            )
            .mappings()
            .all()
        )

    cases_by_batch: dict = {}
    for case in cases:
        cases_by_batch.setdefault(case.batch_id, []).append(case)

    batch_views = []
    totals = {
        "batches": len(batches),
        "planned_cases": 0,
        "with_commit": 0,
        "on_time": 0,
        "late": 0,
        "unconfirmed": 0,
        "no_commit": 0,
        "outcome_resolved": 0,
        "outcome_unresolved": 0,
        "outcome_unscorable": 0,
        "outcome_pending": 0,
    }
    reports_by_batch: dict = {}
    for r in reports:
        reports_by_batch.setdefault(r.batch_id, {})[str(r.horizon_td)] = r.report_version

    for batch in batches:
        batch_cases = cases_by_batch.get(batch.id, [])
        view = {
            "batch_id": str(batch.id),
            "decision_cutoff_utc": batch.decision_cutoff_utc.isoformat(),
            "prediction_deadline_utc": batch.prediction_deadline_utc.isoformat(),
            "entry_date": batch.entry_date.isoformat(),
            "backfilled_plan": batch.backfilled_plan,
            "planned_cases": len(batch_cases),
            "with_commit": 0,
            "on_time": 0,
            "late": 0,
            "unconfirmed": 0,
            "no_commit": 0,
            "outcome_resolved": 0,
            "outcome_unresolved": 0,
            "outcome_unscorable": 0,
            "outcome_pending": 0,
            "reports": reports_by_batch.get(batch.id, {}),
        }
        for case in batch_cases:
            totals["planned_cases"] += 1
            commit = commits.get(case.id)
            if commit is None:
                view["no_commit"] += 1
                totals["no_commit"] += 1
            else:
                view["with_commit"] += 1
                totals["with_commit"] += 1
                confirmation = confirmations.get(commit.id)
                timeliness = (
                    confirmation["timeliness"] if confirmation else "unconfirmed"
                )
                key = timely_key(timeliness)
                view[key] += 1
                totals[key] += 1
            head = heads.get(case.id)
            if head is None:
                view["outcome_pending"] += 1
                totals["outcome_pending"] += 1
            else:
                key = f"outcome_{head.status}"
                view[key] += 1
                totals[key] += 1
        batch_views.append(view)

    return {
        "campaign_id": str(campaign_id),
        "tenant_id": str(campaign.tenant_id),
        "campaign_key": campaign.campaign_key,
        "status": campaign.status,
        "release_id": release,
        "enabled_sources": campaign.enabled_sources,
        "panel_size": len(campaign.panel_security_ids),
        "totals": totals,
        "batches": batch_views,
    }


def timely_key(timeliness: str) -> str:
    """Map a timeliness judgment to the status-view counter key."""
    return {"on_time": "on_time", "late": "late"}.get(timeliness, "unconfirmed")
