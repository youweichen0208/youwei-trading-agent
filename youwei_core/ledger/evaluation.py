"""Batch evaluation reports (S05, campaign-policy §4).

The registered Phase 1A primary metric (D20 only):

    y_i = 1(realized_excess_return_i > 0)      # == 0 counts as false
    d_i = (p_quant_i - y_i)^2 - (p_baseline_i - y_i)^2

The batch point estimate is the mean of d_i over the pairable subset:
cases whose commit is durably confirmed on_time, whose baseline AND
quant_model positions are produced with valid probabilities, and whose
outcome head is resolved. Everything else stays visible in the
coverage breakdown — planned cases without commits, late/uncertain
commits, unavailable sources, unresolved/unscorable outcomes — because
a subset mean is never a claim about the missing cases.

v1 is deliberately descriptive (campaign-policy §4.1): point
estimates and coverage only. Block-bootstrap intervals and any
inferential statements stay disabled until their parameters are
registered and sample conditions met; this module must not grow
significance tests on its own.

Reports are append-only versions per (batch, horizon): the content
fixes the scored case/commit/outcome-revision references, the release
and the scoring code version; a correction appends a new version and
the old report keeps its original references. Identical heads
regenerate identically (content hash, no timestamp inside).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import (
    PRIMARY_HORIZON_TD,
    campaigns,
    evaluation_reports,
    events,
    forecast_batches,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    outcome_revisions,
    predictions,
    research_releases,
)
from youwei_core.ledger.service import decimal_str, sha256_hex

SCORING_CODE_VERSION = "scoring-v1"
NUMERIC_SCALE = Decimal("0.0000000001")

SCORABLE_SOURCES = ("baseline", "quant_model")  # Phase 1A


class EvaluationError(Exception):
    pass


class BatchNotFound(EvaluationError):
    pass


class NotReady(EvaluationError):
    """The horizon's cases have not all reached a definite state:
    either the exit has not passed or cases still lack an outcome
    head (the scheduler keeps retrying; the report never waits
    forever on data — the resolver's grace policy decides)."""


@dataclass
class ReportResult:
    report_id: uuid.UUID
    batch_id: uuid.UUID
    horizon_td: int
    report_version: int
    created: bool
    content_sha256: str
    content: dict


# --- pure scoring --------------------------------------------------------------


def outcome_label(excess_return: Decimal) -> int:
    """target-spec §1: y = 1(realized_excess_return > 0); exactly 0
    counts as false."""
    return 1 if excess_return > 0 else 0


def brier(p: Decimal, y: int) -> Decimal:
    return ((p - y) ** 2).quantize(NUMERIC_SCALE)


def paired_brier_delta(p_baseline, p_quant, y) -> Decimal:
    """d_i: quant Brier minus baseline Brier (smaller is better)."""
    return (brier(p_quant, y) - brier(p_baseline, y)).quantize(NUMERIC_SCALE)


def _mean(values: list[Decimal]) -> Decimal | None:
    """None (NA) when empty — never a fabricated 0."""
    if not values:
        return None
    return (sum(values) / Decimal(len(values))).quantize(NUMERIC_SCALE)


# --- report generation -----------------------------------------------------------


async def generate_batch_report(
    engine: AsyncEngine, batch_id: uuid.UUID, horizon_td: int
) -> ReportResult:
    """Generate (or idempotently return) the batch x horizon report."""
    if horizon_td not in (1, 20, 60):
        raise EvaluationError(f"invalid horizon_td {horizon_td}")

    async with engine.begin() as conn:
        batch = (
            await conn.execute(
                select(forecast_batches).where(forecast_batches.c.id == batch_id)
            )
        ).mappings().one_or_none()
        if batch is None:
            raise BatchNotFound(str(batch_id))
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == batch.campaign_id)
            )
        ).mappings().one()
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.id == campaign.release_row_id
                )
            )
        ).mappings().one()
        db_now = (await conn.execute(select(func.now()))).scalar_one()

        cases = (
            (
                await conn.execute(
                    select(forecast_cases)
                    .where(
                        forecast_cases.c.batch_id == batch_id,
                        forecast_cases.c.horizon_td == horizon_td,
                    )
                    .order_by(forecast_cases.c.security_id)
                )
            )
            .mappings()
            .all()
        )
        if not cases:
            raise EvaluationError(
                f"batch {batch_id} has no cases for horizon {horizon_td}"
            )

        # maturity gate: resolution starts after the planned exit
        latest_exit = max(c.exit_at_utc for c in cases)
        if db_now < latest_exit:
            raise NotReady(
                f"horizon {horizon_td} exits at {latest_exit.isoformat()}; "
                "reports start after the planned exit"
            )

        # outcome heads (latest revision per case)
        heads = (
            (
                await conn.execute(
                    select(outcome_revisions)
                    .where(outcome_revisions.c.case_id.in_([c.id for c in cases]))
                    .prefix_with(
                        "DISTINCT ON (case_id)", dialect="postgresql"
                    )
                    .order_by(
                        outcome_revisions.c.case_id,
                        outcome_revisions.c.revision.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        head_by_case = {h.case_id: h for h in heads}
        pending = [str(c.id) for c in cases if c.id not in head_by_case]
        if pending:
            raise NotReady(
                f"{len(pending)} case(s) still lack an outcome head "
                "(not resolved/unresolved/unscorable yet): "
                f"{sorted(pending)}"
            )

        # commits for the campaign's release + their predictions and
        # durable confirmations
        commits = (
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
        commit_by_case = {cm.case_id: cm for cm in commits}
        preds_by_commit: dict = {}
        if commits:
            pred_rows = (
                (
                    await conn.execute(
                        select(predictions).where(
                            predictions.c.commit_id.in_([cm.id for cm in commits])
                        )
                    )
                )
                .mappings()
                .all()
            )
            for p in pred_rows:
                preds_by_commit.setdefault(p.commit_id, {})[p.source] = p
            confirmations = (
                (
                    await conn.execute(
                        select(
                            forecast_commit_events.c.commit_id,
                            forecast_commit_events.c.payload,
                        )
                        .where(
                            forecast_commit_events.c.commit_id.in_(
                                [cm.id for cm in commits]
                            ),
                            forecast_commit_events.c.event_type
                            == "durable_confirmation",
                        )
                        .order_by(forecast_commit_events.c.occurred_at.desc())
                    )
                )
                .mappings()
                .all()
            )
        else:
            confirmations = []
        confirmed_by_commit = {c.commit_id: c.payload for c in confirmations}

    # ---- build per-case rows (pure computation) -------------------------
    case_rows = []
    coverage = {
        "planned_cases": len(cases),
        "with_commit": 0,
        "on_time_commits": 0,
        "pairable": 0,
        "outcome_status": {"resolved": 0, "unresolved": 0, "unscorable": 0},
        "source_status": {
            s: {"produced": 0, "fallback": 0, "unavailable": 0, "no_position": 0}
            for s in ("baseline", "quant_model", "llm_adjusted")
        },
    }
    brier_values = {s: [] for s in SCORABLE_SOURCES}
    mse_values = {s: [] for s in SCORABLE_SOURCES}
    d_values = []

    for case in cases:
        commit = commit_by_case.get(case.id)
        head = head_by_case[case.id]
        exclusions = []

        timeliness = None
        sources: dict = {}
        if commit is None:
            exclusions.append("no_commit")
            coverage["source_status"]["baseline"]["no_position"] += 1
            coverage["source_status"]["quant_model"]["no_position"] += 1
            coverage["source_status"]["llm_adjusted"]["no_position"] += 1
        else:
            coverage["with_commit"] += 1
            confirmation = confirmed_by_commit.get(commit.id)
            if confirmation is None:
                # generation happens well past the deadline (all cases
                # matured), so an unconfirmed commit is uncertain
                timeliness = "uncertain"
            else:
                timeliness = confirmation["timeliness"]
            if timeliness != "on_time":
                exclusions.append(f"commit_{timeliness}")
            else:
                coverage["on_time_commits"] += 1

            preds = preds_by_commit.get(commit.id, {})
            for source in ("baseline", "quant_model", "llm_adjusted"):
                pred = preds.get(source)
                if pred is None:
                    coverage["source_status"][source]["no_position"] += 1
                    if source in SCORABLE_SOURCES:
                        exclusions.append(f"{source}_not_produced")
                    sources[source] = None
                    continue
                coverage["source_status"][source][pred.source_status] += 1
                sources[source] = {
                    "source_status": pred.source_status,
                    "reason": pred.reason,
                    "p_outperform": (
                        None if pred.p_outperform is None else decimal_str(pred.p_outperform)
                    ),
                    "expected_excess_return": (
                        None
                        if pred.expected_excess_return is None
                        else decimal_str(pred.expected_excess_return)
                    ),
                    "model_version": pred.model_version,
                }
                if source in SCORABLE_SOURCES and pred.source_status != "produced":
                    exclusions.append(f"{source}_not_produced")

        coverage["outcome_status"][head.status] += 1
        outcome_entry = {
            "outcome_id": str(head.id),
            "revision": head.revision,
            "status": head.status,
            "excess_return": (
                None if head.excess_return is None else decimal_str(head.excess_return)
            ),
        }
        y = None
        if head.status != "resolved":
            exclusions.append(f"outcome_{head.status}")
        else:
            y = outcome_label(head.excess_return)

        # per-source scorable sets: on_time commit + produced + resolved
        if commit is not None and timeliness == "on_time" and y is not None:
            for source in SCORABLE_SOURCES:
                pred = (preds_by_commit.get(commit.id) or {}).get(source)
                if pred is not None and pred.source_status == "produced":
                    brier_values[source].append(brier(pred.p_outperform, y))
                    if pred.expected_excess_return is not None:
                        mse_values[source].append(
                            (
                                (pred.expected_excess_return - head.excess_return) ** 2
                            ).quantize(NUMERIC_SCALE)
                        )

        pairable = not exclusions
        d_i = None
        if pairable:
            coverage["pairable"] += 1
            preds = preds_by_commit[commit.id]
            d_i = paired_brier_delta(
                preds["baseline"].p_outperform,
                preds["quant_model"].p_outperform,
                y,
            )
            d_values.append(d_i)

        case_rows.append(
            {
                "case_id": str(case.id),
                "security_id": str(case.security_id),
                "commit_id": None if commit is None else str(commit.id),
                "commit_timeliness": timeliness,
                "sources": sources,
                "outcome": outcome_entry,
                "y": y,
                "exclusions": exclusions,
                "pairable": pairable,
                "d_i": None if d_i is None else decimal_str(d_i),
            }
        )

    metrics = {
        "mean_paired_brier_delta": (
            None if not d_values else decimal_str(_mean(d_values))
        ),
        "paired_n": len(d_values),
        "brier": {
            s: (None if not v else decimal_str(_mean(v))) for s, v in brier_values.items()
        },
        "brier_n": {s: len(v) for s, v in brier_values.items()},
        "mse_expected_excess": {
            s: (None if not v else decimal_str(_mean(v))) for s, v in mse_values.items()
        },
        "rmse_expected_excess": {
            s: (
                None
                if not v
                else decimal_str(_mean(v).sqrt().quantize(NUMERIC_SCALE))
            )
            for s, v in mse_values.items()
        },
        "mse_n": {s: len(v) for s, v in mse_values.items()},
    }

    content = {
        "batch_id": str(batch_id),
        "campaign_id": str(batch.campaign_id),
        "horizon_td": horizon_td,
        "horizon_role": "primary" if horizon_td == PRIMARY_HORIZON_TD else "exploratory",
        "release_id": release.release_id,
        "primary_metric": campaign.primary_metric,
        "scoring_code_version": SCORING_CODE_VERSION,
        "coverage": coverage,
        "metrics": metrics,
        "cases": case_rows,
    }
    content_sha = sha256_hex(content)

    # ---- idempotent append ------------------------------------------------
    async with engine.begin() as conn:
        latest = (
            await conn.execute(
                select(evaluation_reports)
                .where(
                    evaluation_reports.c.batch_id == batch_id,
                    evaluation_reports.c.horizon_td == horizon_td,
                )
                .order_by(evaluation_reports.c.report_version.desc())
                .limit(1)
            )
        ).mappings().first()
        if latest is not None and latest.content_sha256 == content_sha:
            return ReportResult(
                report_id=latest.id,
                batch_id=batch_id,
                horizon_td=horizon_td,
                report_version=latest.report_version,
                created=False,
                content_sha256=content_sha,
                content=latest.content,
            )

        version = 1 if latest is None else latest.report_version + 1
        report_id = uuid.uuid4()
        await conn.execute(
            evaluation_reports.insert().values(
                id=report_id,
                batch_id=batch_id,
                campaign_id=batch.campaign_id,
                horizon_td=horizon_td,
                report_version=version,
                supersedes_report_id=None if latest is None else latest.id,
                release_row_id=campaign.release_row_id,
                scoring_code_version=SCORING_CODE_VERSION,
                content=content,
                content_sha256=content_sha,
            )
        )
        await conn.execute(
            events.insert().values(
                tenant_id=campaign.tenant_id,
                event_type="evaluation.report_generated",
                payload={
                    "report_id": str(report_id),
                    "batch_id": str(batch_id),
                    "horizon_td": horizon_td,
                    "report_version": version,
                },
            )
        )
    return ReportResult(
        report_id=report_id,
        batch_id=batch_id,
        horizon_td=horizon_td,
        report_version=version,
        created=True,
        content_sha256=content_sha,
        content=content,
    )


# --- queries ------------------------------------------------------------------


async def latest_report(
    engine: AsyncEngine, batch_id: uuid.UUID, horizon_td: int
) -> dict | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(evaluation_reports)
                .where(
                    evaluation_reports.c.batch_id == batch_id,
                    evaluation_reports.c.horizon_td == horizon_td,
                )
                .order_by(evaluation_reports.c.report_version.desc())
                .limit(1)
            )
        ).mappings().first()
    if row is None:
        return None
    return {
        "report_id": str(row.id),
        "batch_id": str(row.batch_id),
        "horizon_td": row.horizon_td,
        "report_version": row.report_version,
        "supersedes_report_id": (
            None if row.supersedes_report_id is None else str(row.supersedes_report_id)
        ),
        "release_row_id": str(row.release_row_id),
        "scoring_code_version": row.scoring_code_version,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat(),
        "content": row.content,
    }
