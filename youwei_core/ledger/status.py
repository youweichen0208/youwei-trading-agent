"""Read-only campaign status (S06): the minimal visibility layer.

Shows, per campaign and per batch: planned cases, commits and their
timeliness, outcome head states, and available report versions —
the planned/completed/missing/late/data-quality/scoring-scope view
the S06 checklist asks for. Pure reads; no state changes.
"""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import (
    campaigns,
    evaluation_reports,
    forecast_batches,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    monthly_summary_reports,
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


# --- saved report reads (S10 slice 1) --------------------------------------
#
# Dashboard reads answer SAVED reports only: version, content sha256,
# code versions and the frozen content. GET never regenerates, and an
# old version keeps the outcome revisions it was computed from —
# corrections append a new version; they never rewrite history.


def _report_view(row, latest_version: int) -> dict:
    return {
        "report_id": str(row.id),
        "campaign_id": str(row.campaign_id),
        "report_version": row.report_version,
        "supersedes_report_id": (
            None if row.supersedes_report_id is None else str(row.supersedes_report_id)
        ),
        "release_row_id": str(row.release_row_id),
        "scoring_code_version": row.scoring_code_version,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat(),
        "is_latest_version": row.report_version == latest_version,
        "content": row.content,
    }


async def _campaign_tenant(engine: AsyncEngine, campaign_id: uuid.UUID):
    async with engine.begin() as conn:
        return (
            await conn.execute(
                select(campaigns.c.tenant_id).where(campaigns.c.id == campaign_id)
            )
        ).scalar_one_or_none()


async def batch_report_view(
    engine: AsyncEngine,
    campaign_id: uuid.UUID,
    batch_id: uuid.UUID,
    horizon_td: int,
    version: int | None = None,
) -> dict | None:
    """One SAVED batch report. None when the campaign, batch, horizon
    report or version does not exist, or the batch belongs to another
    campaign. ``version=None`` reads the latest."""
    tenant = await _campaign_tenant(engine, campaign_id)
    if tenant is None:
        return None
    async with engine.begin() as conn:
        stmt = (
            select(evaluation_reports)
            .join(forecast_batches, forecast_batches.c.id == evaluation_reports.c.batch_id)
            .where(
                evaluation_reports.c.batch_id == batch_id,
                forecast_batches.c.campaign_id == campaign_id,
                evaluation_reports.c.horizon_td == horizon_td,
            )
        )
        if version is not None:
            stmt = stmt.where(evaluation_reports.c.report_version == version)
        else:
            stmt = stmt.order_by(evaluation_reports.c.report_version.desc())
        row = (await conn.execute(stmt.limit(1))).mappings().one_or_none()
        if row is None:
            return None
        latest_version = (
            await conn.execute(
                select(func.max(evaluation_reports.c.report_version)).where(
                    evaluation_reports.c.batch_id == batch_id,
                    evaluation_reports.c.horizon_td == horizon_td,
                )
            )
        ).scalar_one()
    return {
        **_report_view(row, latest_version),
        "tenant_id": str(tenant),
        "batch_id": str(batch_id),
        "horizon_td": horizon_td,
    }


async def monthly_report_view(
    engine: AsyncEngine,
    campaign_id: uuid.UUID,
    month: date,
    version: int | None = None,
) -> dict | None:
    """One SAVED monthly summary. None when the campaign, month report
    or version does not exist. ``version=None`` reads the latest."""
    tenant = await _campaign_tenant(engine, campaign_id)
    if tenant is None:
        return None
    async with engine.begin() as conn:
        stmt = select(monthly_summary_reports).where(
            monthly_summary_reports.c.campaign_id == campaign_id,
            monthly_summary_reports.c.month == month,
        )
        if version is not None:
            stmt = stmt.where(monthly_summary_reports.c.report_version == version)
        else:
            stmt = stmt.order_by(monthly_summary_reports.c.report_version.desc())
        row = (await conn.execute(stmt.limit(1))).mappings().one_or_none()
        if row is None:
            return None
        latest_version = (
            await conn.execute(
                select(func.max(monthly_summary_reports.c.report_version)).where(
                    monthly_summary_reports.c.campaign_id == campaign_id,
                    monthly_summary_reports.c.month == month,
                )
            )
        ).scalar_one()
    return {
        **_report_view(row, latest_version),
        "tenant_id": str(tenant),
        "month": month.isoformat(),
        "monthly_code_version": row.monthly_code_version,
    }
