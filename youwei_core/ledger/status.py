"""Read-only campaign status (S06): the minimal visibility layer.

Shows, per campaign and per batch: planned cases, commits and their
timeliness, outcome head states, and available report versions —
the planned/completed/missing/late/data-quality/scoring-scope view
the S06 checklist asks for. Pure reads; no state changes.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import func, or_, select
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
    predictions,
    research_releases,
    securities,
    security_identities,
)


async def campaign_status(engine: AsyncEngine, campaign_id: uuid.UUID) -> dict | None:
    """Campaign-level status view. None when the campaign does not
    exist. Timeliness here is the raw confirmation state (on_time /
    late / unconfirmed); the evaluation report derives the
    conservative uncertain judgment."""
    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()
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
        # lifecycle phase against the database clock: a future cutoff is
        # a normal wait, not a failure; a closed window without a single
        # commit is a missed run (backfilled batches stay in the
        # denominator as explicit records)
        if db_now < batch.decision_cutoff_utc:
            phase = "awaiting_cutoff"
        elif db_now < batch.prediction_deadline_utc:
            phase = "in_window"
        elif any(commits.get(case.id) is not None for case in batch_cases):
            phase = "sealed"
        else:
            phase = "missed"
        view = {
            "batch_id": str(batch.id),
            "phase": phase,
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

    # frozen-plan denominator: the campaign's registered weekly plan is
    # the coverage denominator, not the batches that happen to exist
    # yet — future cutoffs await pre-registration (normal), past cutoffs
    # without a batch mean the scheduler is behind
    planned = [datetime.fromisoformat(c) for c in (campaign.planned_cutoffs or [])]
    registered = {b.decision_cutoff_utc for b in batches}
    horizons = sorted({int(s["horizon_td"]) for s in campaign.target_specs})
    panel_size = len(campaign.panel_security_ids)

    return {
        "campaign_id": str(campaign_id),
        "tenant_id": str(campaign.tenant_id),
        "campaign_key": campaign.campaign_key,
        "status": campaign.status,
        "release_id": release,
        "enabled_sources": campaign.enabled_sources,
        "panel_size": len(campaign.panel_security_ids),
        "plan": {
            "planned_batches": len(planned),
            "panel_size": panel_size,
            "horizons": horizons,
            "planned_cases": len(planned) * panel_size * len(horizons),
            "batches": {
                "registered": len(batches),
                "pending_registration": sum(
                    1 for c in planned if c > db_now and c not in registered
                ),
                "overdue_unregistered": sum(
                    1 for c in planned if c <= db_now and c not in registered
                ),
                "backfilled": sum(1 for b in batches if b.backfilled_plan),
            },
        },
        "totals": totals,
        "batches": batch_views,
    }


def timely_key(timeliness: str) -> str:
    """Map a timeliness judgment to the status-view counter key."""
    return {"on_time": "on_time", "late": "late"}.get(timeliness, "unconfirmed")


# --- case listing (S10 slice 2) ----------------------------------------------


async def campaign_cases_view(
    engine: AsyncEngine,
    campaign_id: uuid.UUID,
    *,
    batch_id: uuid.UUID | None = None,
    horizon_td: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict | None:
    """Read-only, paginated case listing for the dashboard.

    Per case: the sealed commit with its per-source positions (in
    Phase 1A llm_adjusted is sealed unavailable/not_enabled and
    surfaces as such), the CURRENT outcome head, and the outcome
    revision the LATEST batch report scored this case against — the
    two can differ after a correction appends a new head.
    Pending states (no commit / no outcome / no report yet) are nulls,
    never failures and never zeros. None when the campaign does not
    exist."""
    async with engine.begin() as conn:
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == campaign_id)
            )
        ).mappings().one_or_none()
        if campaign is None:
            return None
        db_now = (await conn.execute(select(func.now()))).scalar_one()

        cond = [forecast_cases.c.campaign_id == campaign_id]
        if batch_id is not None:
            cond.append(forecast_cases.c.batch_id == batch_id)
        if horizon_td is not None:
            cond.append(forecast_cases.c.horizon_td == horizon_td)
        total = (
            await conn.execute(
                select(func.count()).select_from(forecast_cases).where(*cond)
            )
        ).scalar_one()
        cases = (
            (
                await conn.execute(
                    select(forecast_cases)
                    .where(*cond)
                    .order_by(
                        forecast_cases.c.decision_cutoff_utc,
                        forecast_cases.c.horizon_td,
                        forecast_cases.c.security_id,
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .mappings()
            .all()
        )

        case_ids = [c.id for c in cases]
        symbols: dict = {}
        heads: dict = {}
        commit_rows: dict = {}
        source_rows: dict = {}
        timeliness: dict = {}
        scored: dict = {}
        if case_ids:
            sec_ids = {c.security_id for c in cases} | {
                c.benchmark_security_id for c in cases
            }
            for ident in (
                await conn.execute(
                    select(
                        security_identities.c.security_id,
                        security_identities.c.identifier,
                    ).where(
                        security_identities.c.security_id.in_(sec_ids),
                        security_identities.c.identifier_type == "ticker",
                        security_identities.c.valid_to.is_(None),
                    )
                )
            ).mappings().all():
                symbols[ident.security_id] = ident.identifier

            heads = {
                h.case_id: h
                for h in (
                    await conn.execute(
                        select(outcome_revisions)
                        .where(outcome_revisions.c.case_id.in_(case_ids))
                        .prefix_with("DISTINCT ON (case_id)", dialect="postgresql")
                        .order_by(
                            outcome_revisions.c.case_id,
                            outcome_revisions.c.revision.desc(),
                        )
                    )
                ).mappings().all()
            }

            commit_rows = {
                cm.case_id: cm
                for cm in (
                    await conn.execute(
                        select(forecast_commits).where(
                            forecast_commits.c.case_id.in_(case_ids),
                            forecast_commits.c.release_row_id == campaign.release_row_id,
                        )
                    )
                ).mappings().all()
            }
            if commit_rows:
                commit_ids = [cm.id for cm in commit_rows.values()]
                for pred in (
                    await conn.execute(
                        select(predictions)
                        .where(predictions.c.commit_id.in_(commit_ids))
                        .order_by(predictions.c.commit_id, predictions.c.source)
                    )
                ).mappings().all():
                    source_rows.setdefault(pred.commit_id, {})[pred.source] = pred
                for r in (
                    await conn.execute(
                        select(
                            forecast_commit_events.c.commit_id,
                            forecast_commit_events.c.payload,
                        )
                        .where(
                            forecast_commit_events.c.commit_id.in_(commit_ids),
                            forecast_commit_events.c.event_type == "durable_confirmation",
                        )
                        .order_by(forecast_commit_events.c.occurred_at.desc())
                    )
                ).mappings().all():
                    timeliness.setdefault(r.commit_id, r.payload.get("timeliness"))

            # the outcome revision the LATEST report scored each case
            # against (per batch + horizon of the cases on this page)
            batch_horizons = {(c.batch_id, c.horizon_td) for c in cases}
            if batch_horizons:
                or_clauses = [
                    (evaluation_reports.c.batch_id == b)
                    & (evaluation_reports.c.horizon_td == h)
                    for b, h in batch_horizons
                ]
                report_rows = (
                    await conn.execute(
                        select(
                            evaluation_reports.c.batch_id,
                            evaluation_reports.c.horizon_td,
                            evaluation_reports.c.report_version,
                            evaluation_reports.c.content,
                        )
                        .where(or_(*or_clauses))
                        .order_by(evaluation_reports.c.report_version.desc())
                    )
                ).mappings().all()
                latest_report: dict = {}
                for r in report_rows:
                    latest_report.setdefault((r.batch_id, r.horizon_td), r)
                for r in latest_report.values():
                    for case_row in r.content.get("cases", []):
                        cid = case_row.get("case_id")
                        outcome = case_row.get("outcome") or {}
                        if outcome.get("revision") is not None:
                            scored[cid] = outcome["revision"]

    items = []
    for c in cases:
        commit = commit_rows.get(c.id)
        head = heads.get(c.id)
        commit_view = None
        if commit is not None:
            preds = source_rows.get(commit.id, {})
            commit_view = {
                "commit_id": str(commit.id),
                "sealed_at": commit.sealed_at.isoformat(),
                "timeliness": timeliness.get(commit.id, "unconfirmed"),
                "sources": {
                    source: {
                        "source_status": pred.source_status,
                        "reason": pred.reason,
                        "p_outperform": (
                            None if pred.p_outperform is None else float(pred.p_outperform)
                        ),
                        "expected_excess_return": (
                            None
                            if pred.expected_excess_return is None
                            else float(pred.expected_excess_return)
                        ),
                        "model_version": pred.model_version,
                    }
                    for source, pred in preds.items()
                },
            }
        items.append(
            {
                "case_id": str(c.id),
                "batch_id": str(c.batch_id),
                "horizon_td": c.horizon_td,
                "security_id": str(c.security_id),
                "security_symbol": symbols.get(c.security_id),
                "benchmark_security_id": str(c.benchmark_security_id),
                "decision_cutoff_utc": c.decision_cutoff_utc.isoformat(),
                "prediction_deadline_utc": c.prediction_deadline_utc.isoformat(),
                "entry_at_utc": c.entry_at_utc.isoformat(),
                "exit_at_utc": c.exit_at_utc.isoformat(),
                "label_mature": c.exit_at_utc <= db_now,
                "commit": commit_view,
                "outcome_head": (
                    None
                    if head is None
                    else {
                        "outcome_id": str(head.id),
                        "revision": head.revision,
                        "status": head.status,
                        "excess_return": (
                            None
                            if head.excess_return is None
                            else float(head.excess_return)
                        ),
                    }
                ),
                "scored_against_revision": scored.get(str(c.id)),
            }
        )

    pages = (total + page_size - 1) // page_size
    return {
        "campaign_id": str(campaign_id),
        "tenant_id": str(campaign.tenant_id),
        "enabled_sources": campaign.enabled_sources,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "items": items,
    }


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
