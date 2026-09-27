"""Monthly summary reports (S06, campaign-policy §4.2).

Batches are attributed to the month of their decision cutoff; the
monthly summary aggregates the REGISTERED batch D20 point estimates
(campaign-policy §4: the batch mean of paired Brier deltas) with
batch equal weight — never treating a week's 20 same-window securities
as 20 independent market cycles. Batches without a pairable point
estimate show NA; nothing is ever filled with 0.

Versioning follows the registered protocol: the scheduled v1 lands on
the first regular trading day of the next month at 06:00 ET with data
through the previous natural month end; as labels mature or get
corrected later, new versions append (supersedes chain) and old
versions keep their original references. Each version fixes the
batch-report versions it aggregates plus the month's case/commit/
outcome-head references; identical state regenerates identically
(content hash, no generation timestamp inside).

v1 is descriptive only (campaign-policy §4.1): point estimates and
coverage. Block-bootstrap intervals and inferential statements stay
disabled until their parameters are registered — this module must not
grow significance tests on its own.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import CalendarNotBuilt, VENUE
from youwei_core.db.meta import (
    PRIMARY_HORIZON_TD,
    calendar_days,
    campaigns,
    evaluation_reports,
    events,
    forecast_batches,
    forecast_cases,
    forecast_commits,
    monthly_summary_reports,
    outcome_revisions,
    research_releases,
)
from youwei_core.ledger.evaluation import SCORING_CODE_VERSION
from youwei_core.ledger.service import decimal_str, sha256_hex

MONTHLY_CODE_VERSION = "monthly-summary-v1"
NUMERIC_SCALE = Decimal("0.0000000001")
ET = ZoneInfo("America/New_York")


class MonthlyError(Exception):
    pass


class CampaignNotFound(MonthlyError):
    pass


class NotReady(MonthlyError):
    """The attribution month has not fully ended yet."""


@dataclass
class MonthState:
    """One load of everything a month's report derives from: the
    month's batches, their latest D20 reports, and the D20
    case/commit/outcome-head references with the current maturity
    vector. Light enough to digest without building content."""

    month: date
    campaign: dict
    release: dict
    due_at: datetime
    batches: list
    reports: dict


@dataclass
class MonthlyReportResult:
    report_id: uuid.UUID
    campaign_id: uuid.UUID
    month: date
    report_version: int
    created: bool
    content_sha256: str
    inputs_sha256: str
    content: dict


# --- month arithmetic (ET frame, time-protocol §1) -------------------------------


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def month_bounds_et(month: date) -> tuple[datetime, datetime]:
    """[first instant of the month, first instant of the next month)
    in the New York frame, as UTC instants."""
    start = datetime.combine(month, time(0, 0), tzinfo=ET).astimezone(UTC)
    end = datetime.combine(_next_month(month), time(0, 0), tzinfo=ET).astimezone(UTC)
    return start, end


async def _first_trading_day_of(engine: AsyncEngine, month: date) -> date:
    async with engine.begin() as conn:
        d = (
            await conn.execute(
                select(func.min(calendar_days.c.date)).where(
                    calendar_days.c.venue == VENUE,
                    calendar_days.c.is_trading.is_(True),
                    calendar_days.c.date >= month,
                    calendar_days.c.date < _next_month(month),
                )
            )
        ).scalar_one_or_none()
    if d is None:
        raise CalendarNotBuilt(
            f"no trading day found in {month.strftime('%Y-%m')}: "
            "the calendar range was never built"
        )
    return d


async def monthly_due_at(engine: AsyncEngine, month: date) -> datetime:
    """The registered monthly schedule (campaign-policy §4.2, an
    implementation default fixed here): 06:00 ET on the first regular
    trading day of the month AFTER `month`."""
    d = await _first_trading_day_of(engine, _next_month(month))
    return datetime.combine(d, time(6, 0), tzinfo=ET).astimezone(UTC)


# --- state loading ----------------------------------------------------------------


async def load_month_state(
    engine: AsyncEngine, campaign_id: uuid.UUID, month: date, db_now: datetime
) -> MonthState | None:
    """Load the month's report inputs. None when the campaign has no
    batch with a cutoff in this month (nothing to summarize)."""
    if month.day != 1:
        raise MonthlyError("month must be given as its first day")
    start_utc, end_utc = month_bounds_et(month)

    async with engine.begin() as conn:
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == campaign_id)
            )
        ).mappings().one_or_none()
        if campaign is None:
            raise CampaignNotFound(str(campaign_id))
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.id == campaign.release_row_id
                )
            )
        ).mappings().one()
        batch_rows = (
            (
                await conn.execute(
                    select(forecast_batches)
                    .where(
                        forecast_batches.c.campaign_id == campaign_id,
                        forecast_batches.c.decision_cutoff_utc >= start_utc,
                        forecast_batches.c.decision_cutoff_utc < end_utc,
                    )
                    .order_by(forecast_batches.c.decision_cutoff_utc)
                )
            )
            .mappings()
            .all()
        )
        if not batch_rows:
            return None
        batch_ids = [b.id for b in batch_rows]

        case_rows = (
            (
                await conn.execute(
                    select(
                        forecast_cases.c.id,
                        forecast_cases.c.batch_id,
                        forecast_cases.c.security_id,
                        forecast_cases.c.exit_at_utc,
                    )
                    .where(
                        forecast_cases.c.batch_id.in_(batch_ids),
                        forecast_cases.c.horizon_td == PRIMARY_HORIZON_TD,
                    )
                    .order_by(forecast_cases.c.security_id)
                )
            )
            .mappings()
            .all()
        )
        case_ids = [c.id for c in case_rows]

        head_rows = []
        if case_ids:
            head_rows = (
                (
                    await conn.execute(
                        select(
                            outcome_revisions.c.case_id,
                            outcome_revisions.c.id,
                            outcome_revisions.c.revision,
                            outcome_revisions.c.status,
                        )
                        .where(outcome_revisions.c.case_id.in_(case_ids))
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
        heads_by_case = {h.case_id: h for h in head_rows}

        commit_rows = (
            (
                await conn.execute(
                    select(forecast_commits.c.case_id, forecast_commits.c.id).where(
                        forecast_commits.c.case_id.in_(case_ids),
                        forecast_commits.c.release_row_id == campaign.release_row_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        commit_by_case = {c.case_id: str(c.id) for c in commit_rows}

        report_rows = (
            (
                await conn.execute(
                    select(
                        evaluation_reports.c.id,
                        evaluation_reports.c.batch_id,
                        evaluation_reports.c.report_version,
                        evaluation_reports.c.content_sha256,
                        evaluation_reports.c.content,
                    )
                    .where(
                        evaluation_reports.c.batch_id.in_(batch_ids),
                        evaluation_reports.c.horizon_td == PRIMARY_HORIZON_TD,
                    )
                    .prefix_with("DISTINCT ON (batch_id)", dialect="postgresql")
                    .order_by(
                        evaluation_reports.c.batch_id,
                        evaluation_reports.c.report_version.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        reports = {
            str(r.batch_id): {
                "id": str(r.id),
                "report_version": r.report_version,
                "content_sha256": r.content_sha256,
                "content": r.content,
            }
            for r in report_rows
        }

    cases_by_batch: dict[str, list] = {}
    for c in case_rows:
        head = heads_by_case.get(c.id)
        cases_by_batch.setdefault(str(c.batch_id), []).append(
            {
                "case_id": str(c.id),
                "security_id": str(c.security_id),
                "commit_id": commit_by_case.get(c.id),
                "head_id": None if head is None else str(head.id),
                "head_revision": None if head is None else head.revision,
                "head_status": None if head is None else head.status,
                "matured": c.exit_at_utc <= db_now,
            }
        )

    due_at = await monthly_due_at(engine, month)
    batches = [
        {
            "batch_id": str(b.id),
            "decision_cutoff_utc": b.decision_cutoff_utc.isoformat(),
            "backfilled_plan": bool(b.backfilled_plan),
            "d20_report_id": reports.get(str(b.id), {}).get("id"),
            "d20_report_version": reports.get(str(b.id), {}).get("report_version"),
            "cases": cases_by_batch.get(str(b.id), []),
        }
        for b in batch_rows
    ]
    return MonthState(
        month=month,
        campaign=dict(campaign),
        release=dict(release),
        due_at=due_at,
        batches=batches,
        reports=reports,
    )


def monthly_input_digest(state: MonthState) -> str:
    """The regeneration gate: a hash over everything the content
    derives from — the month's batches, their latest D20 report
    versions, the case/commit/head references, the maturity vector and
    the schedule — salted with the code versions. Unchanged digest and
    an existing report means regeneration would produce identical
    content; the tick skips it. The db_now instant itself is NOT an
    input: only the derived maturity booleans are."""
    return sha256_hex(
        {
            "monthly_code_version": MONTHLY_CODE_VERSION,
            "scoring_code_version": SCORING_CODE_VERSION,
            "campaign_id": str(state.campaign["id"]),
            "month": state.month.isoformat(),
            "due_at": state.due_at.isoformat(),
            "batches": state.batches,
        }
    )


# --- report generation --------------------------------------------------------------


def _build_content(state: MonthState, month: date) -> dict:
    campaign, release = state.campaign, state.release
    batch_entries = []
    point_values: list[Decimal] = []
    securities: set[str] = set()
    matured_labels = 0

    for b in state.batches:
        report = state.reports.get(b["batch_id"])
        heads = {"resolved": 0, "unresolved": 0, "unscorable": 0, "no_head": 0}
        matured = 0
        for case in b["cases"]:
            status = case["head_status"] or "no_head"
            heads[status] += 1
            if case["matured"]:
                matured += 1
            if status == "resolved":
                matured_labels += 1
            securities.add(case["security_id"])
        point = None
        if report is not None:
            metrics = report["content"]["metrics"]
            if metrics["paired_n"] > 0 and metrics["mean_paired_brier_delta"] is not None:
                point = metrics["mean_paired_brier_delta"]
                point_values.append(Decimal(point))
        batch_entries.append(
            {
                "batch_id": b["batch_id"],
                "decision_cutoff_utc": b["decision_cutoff_utc"],
                "backfilled_plan": b["backfilled_plan"],
                "d20_report": None
                if report is None
                else {
                    "report_id": report["id"],
                    "report_version": report["report_version"],
                    "content_sha256": report["content_sha256"],
                    "pairable_n": report["content"]["metrics"]["paired_n"],
                    "mean_paired_brier_delta": report["content"]["metrics"][
                        "mean_paired_brier_delta"
                    ],
                },
                "point_estimate": point,  # None = NA, never 0
                "planned_cases": len(b["cases"]),
                "matured_cases": matured,
                "unmatured_cases": len(b["cases"]) - matured,
                "outcome_heads": heads,
                "cases": b["cases"],
            }
        )

    monthly_mean = None
    if point_values:
        monthly_mean = decimal_str(
            (sum(point_values) / Decimal(len(point_values))).quantize(NUMERIC_SCALE)
        )
    _, month_end = month_bounds_et(month)
    return {
        "campaign_id": str(campaign["id"]),
        "campaign_key": campaign["campaign_key"],
        "month": month.isoformat(),
        "release_id": release["release_id"],
        "release_content_sha256": release["release_content_sha256"],
        "primary_metric": campaign["primary_metric"],
        "primary_horizon_td": PRIMARY_HORIZON_TD,
        "scoring_code_version": SCORING_CODE_VERSION,
        "monthly_code_version": MONTHLY_CODE_VERSION,
        "schedule": {
            "data_cutoff": month_end.isoformat(),
            "due_at": state.due_at.isoformat(),
            "note": (
                "v1 generates at due_at with data through the previous natural "
                "month end; later versions append as labels mature or get corrected"
            ),
        },
        "aggregation": "batch-equal-weighted mean of registered batch D20 point "
        "estimates; batches without a pairable point estimate are NA, never 0",
        "batches": batch_entries,
        "metrics": {
            "monthly_mean_paired_brier_delta": monthly_mean,
            "planned_batches": len(batch_entries),
            "scorable_batches": len(point_values),
        },
        "coverage": {
            "securities": len(securities),
            "planned_batches": len(batch_entries),
            "scorable_batches": len(point_values),
            "matured_labels": matured_labels,
        },
    }


async def generate_monthly_report(
    engine: AsyncEngine, campaign_id: uuid.UUID, month: date
) -> MonthlyReportResult:
    """Generate (or idempotently return) the campaign's monthly
    summary for an ended month. The tick additionally enforces the
    registered schedule (due_at) before calling this."""
    if month.day != 1:
        raise MonthlyError("month must be given as its first day")
    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()
    _, month_end = month_bounds_et(month)
    if db_now < month_end:
        raise NotReady(
            f"month {month.strftime('%Y-%m')} has not ended yet "
            f"(ends {month_end.isoformat()})"
        )

    state = await load_month_state(engine, campaign_id, month, db_now)
    if state is None:
        raise MonthlyError(
            f"campaign {campaign_id} has no batch with a cutoff in "
            f"{month.strftime('%Y-%m')}"
        )

    content = _build_content(state, month)
    content_sha = sha256_hex(content)
    digest = monthly_input_digest(state)

    async with engine.begin() as conn:
        latest = (
            await conn.execute(
                select(monthly_summary_reports)
                .where(
                    monthly_summary_reports.c.campaign_id == campaign_id,
                    monthly_summary_reports.c.month == month,
                )
                .order_by(monthly_summary_reports.c.report_version.desc())
                .limit(1)
            )
        ).mappings().first()
        if latest is not None and latest.content_sha256 == content_sha:
            return MonthlyReportResult(
                report_id=latest.id,
                campaign_id=campaign_id,
                month=month,
                report_version=latest.report_version,
                created=False,
                content_sha256=content_sha,
                inputs_sha256=digest,
                content=latest.content,
            )

        version = 1 if latest is None else latest.report_version + 1
        report_id = uuid.uuid4()
        await conn.execute(
            monthly_summary_reports.insert().values(
                id=report_id,
                campaign_id=campaign_id,
                month=month,
                report_version=version,
                supersedes_report_id=None if latest is None else latest.id,
                release_row_id=state.campaign["release_row_id"],
                scoring_code_version=SCORING_CODE_VERSION,
                monthly_code_version=MONTHLY_CODE_VERSION,
                content=content,
                content_sha256=content_sha,
            )
        )
        await conn.execute(
            events.insert().values(
                tenant_id=state.campaign["tenant_id"],
                event_type="evaluation.monthly_report_generated",
                payload={
                    "report_id": str(report_id),
                    "campaign_id": str(campaign_id),
                    "month": month.isoformat(),
                    "report_version": version,
                },
            )
        )
    return MonthlyReportResult(
        report_id=report_id,
        campaign_id=campaign_id,
        month=month,
        report_version=version,
        created=True,
        content_sha256=content_sha,
        inputs_sha256=digest,
        content=content,
    )


# --- queries ------------------------------------------------------------------


async def latest_monthly_report(
    engine: AsyncEngine, campaign_id: uuid.UUID, month: date
) -> dict | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(monthly_summary_reports)
                .where(
                    monthly_summary_reports.c.campaign_id == campaign_id,
                    monthly_summary_reports.c.month == month,
                )
                .order_by(monthly_summary_reports.c.report_version.desc())
                .limit(1)
            )
        ).mappings().first()
    if row is None:
        return None
    return {
        "report_id": str(row.id),
        "campaign_id": str(row.campaign_id),
        "month": row.month.isoformat(),
        "report_version": row.report_version,
        "supersedes_report_id": (
            None if row.supersedes_report_id is None else str(row.supersedes_report_id)
        ),
        "release_row_id": str(row.release_row_id),
        "scoring_code_version": row.scoring_code_version,
        "monthly_code_version": row.monthly_code_version,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat(),
        "content": row.content,
    }
