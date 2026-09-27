"""S06b: monthly summary reports (campaign-policy §4.2).

Acceptance:
- batches attribute to their cutoff month; the summary aggregates the
  registered batch D20 point estimates batch-equal-weighted, listing
  securities, planned batches, scorable batches and matured labels
- batches without a pairable point estimate (not yet reported, no
  pairable case, missed weeks) show NA and never dilute the mean
- the schedule default: 06:00 ET on the first regular trading day of
  the next month (holiday observance and DST included)
- a month that has not ended is NotReady; later maturation or
  corrections append new versions while old versions keep their
  references
- identical state regenerates identically; append-only at the DB
  level like the rest of the ledger
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.ledger.evaluation import generate_batch_report
from youwei_core.ledger.monthly import (
    MonthlyError,
    NotReady,
    generate_monthly_report,
    latest_monthly_report,
    monthly_due_at,
)
from youwei_core.ledger.outcomes import resolve_outcome
from youwei_core.ledger.scheduler import scheduler_tick
from youwei_core.ledger.sealing import SealRequest, SourcePrediction, seal_commit
from youwei_core.ledger.service import plan_batch
from test_data_pit import asyncio_sleep
from test_ledger_campaign import _build_calendar, _setup
from test_ledger_outcomes import _ingest_window, _retarget_case, _window_dates
from test_ledger_seal import _attempt, _quant_evidence_for, _shift_case_window

ET = ZoneInfo("America/New_York")


def _month_of(cutoff: datetime) -> date:
    # cutoffs are Saturday 06:00 ET: the ET date equals the UTC date
    return cutoff.date().replace(day=1)


def _prev_month(month: date) -> date:
    return (month - timedelta(days=1)).replace(day=1)


def _pick_past_month() -> tuple[date, list[datetime]]:
    """A fully ended month (end > 3 weeks past, so the scheduled
    generation moment has passed) holding >= 3 of the recent
    Saturdays, with its cutoffs ascending."""
    now = datetime.now(UTC)
    cutoffs = [next_weekly_cutoff(now - timedelta(weeks=k)) for k in range(1, 11)]
    by_month: dict[date, list[datetime]] = {}
    for c in cutoffs:
        by_month.setdefault(_month_of(c), []).append(c)
    for month in sorted(by_month, reverse=True):
        if len(by_month[month]) < 3:
            continue
        month_end = datetime.combine(
            (month.replace(day=28) + timedelta(days=4)).replace(day=1),
            time(0, 0),
            tzinfo=ET,
        ).astimezone(UTC)
        if (now - month_end).days > 21:
            return month, sorted(by_month[month])
    raise AssertionError("no suitable past month within the last 10 weeks")


async def _seal_with_quant(engine, tenant_id, case_id, p_quant):
    """Seal a case's three positions directly with hand-specified
    probabilities (the monthly layer aggregates reports; the batch
    layer's own tests cover the real sealing path)."""
    claimed = await _attempt(engine, tenant_id)
    request = SealRequest(
        case_id=case_id,
        release_id="rel-test-v1",
        sources=[
            SourcePrediction(
                source="baseline",
                source_status="produced",
                p_outperform=0.5,
                expected_excess_return=0.0,
                model_version="baseline-constant-v0",
            ),
            SourcePrediction(
                source="quant_model",
                source_status="produced",
                p_outperform=p_quant,
                expected_excess_return=0.0,
                model_version="quant-momentum-v0",
                evidence_snapshot_id=await _quant_evidence_for(engine, case_id),
            ),
            SourcePrediction(
                source="llm_adjusted",
                source_status="unavailable",
                reason="not_enabled",
            ),
        ],
        input_manifest={"code_version": "monthly-test-v1"},
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
    )
    return await seal_commit(engine, request)


async def _month_fixture(engine, tenant_id, *, quant_ps=(0.5, 0.6)):
    """One past month holding: one sealed+resolved D20 batch per p
    value (point estimates 0.0 and 0.11 with y=0), plus one
    planned-but-missed batch. Shared flat bars make every excess
    return exactly 0."""
    ctx = await _setup(engine, tenant_id, n_panel=1)
    month, cutoffs = _pick_past_month()

    _, _, dates = await _window_dates(engine, exit_sessions_back=1, span=35)
    await _ingest_window(engine, "S0", ctx["panel"][0], dates)
    await _ingest_window(
        engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    entry_date, exit_date, _ = await _window_dates(engine, exit_sessions_back=10, span=20)

    batch_ids, d20_ids = [], []
    for cutoff, p in zip(cutoffs[:2], quant_ps):
        plan = await plan_batch(
            engine,
            ctx["campaign"].campaign_id,
            decision_cutoff=cutoff,
            backfilled_plan=True,
        )
        async with engine.begin() as conn:
            d20 = (
                await conn.execute(
                    text(
                        "SELECT id FROM forecast_cases WHERE batch_id = :b "
                        "AND horizon_td = 20"
                    ),
                    {"b": str(plan.batch_id)},
                )
            ).scalar_one()
        # open the seal window around the database clock (fixture; the
        # batch row keeps its real past-month cutoff for attribution)
        await _shift_case_window(engine, d20, (timedelta(hours=-1), timedelta(hours=1)))
        await _seal_with_quant(engine, tenant_id, d20, p)
        await _retarget_case(engine, d20, entry_date, exit_date)
        await resolve_outcome(engine, d20)  # flat bars -> excess 0 -> y = 0
        report = await generate_batch_report(engine, plan.batch_id, 20)
        assert report.created
        batch_ids.append(plan.batch_id)
        d20_ids.append(d20)

    # a missed week in the same month: planned, never run, no report
    missed = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=cutoffs[2],
        backfilled_plan=True,
    )
    return {
        "ctx": ctx,
        "month": month,
        "batch_ids": batch_ids,
        "d20_ids": d20_ids,
        "missed_batch_id": missed.batch_id,
        "dates": dates,
    }


# --- schedule --------------------------------------------------------------------


async def test_monthly_due_at_first_trading_day_06_et(db_engine):
    await _build_calendar(db_engine)
    # Sep 2026 -> first trading day of Oct is Thu Oct 1 (EDT, UTC-4)
    due = await monthly_due_at(db_engine, date(2026, 9, 1))
    assert due == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    # Dec 2026 -> Jan 1 2027 is a Friday holiday; first trading day is
    # Mon Jan 4 (EST, UTC-5)
    due2 = await monthly_due_at(db_engine, date(2026, 12, 1))
    assert due2 == datetime(2027, 1, 4, 11, 0, tzinfo=UTC)


# --- aggregation ------------------------------------------------------------------


async def test_monthly_summary_aggregates_batch_point_estimates(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id)
    campaign_id = f["ctx"]["campaign"].campaign_id

    result = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert result.created and result.report_version == 1
    content = result.content

    assert content["month"] == f["month"].isoformat()
    # batch-equal-weighted mean of the registered point estimates
    # (0.0000000000 and 0.1100000000, hand-computed from p and y=0)
    assert content["metrics"]["monthly_mean_paired_brier_delta"] == "0.0550000000"
    assert content["metrics"]["planned_batches"] == 3
    assert content["metrics"]["scorable_batches"] == 2
    assert content["coverage"]["securities"] == 1
    assert content["coverage"]["planned_batches"] == 3
    assert content["coverage"]["scorable_batches"] == 2
    assert content["coverage"]["matured_labels"] == 2

    by_batch = {b["batch_id"]: b for b in content["batches"]}
    assert by_batch[str(f["batch_ids"][0])]["point_estimate"] == "0.0000000000"
    assert by_batch[str(f["batch_ids"][1])]["point_estimate"] == "0.1100000000"
    for bid in f["batch_ids"]:
        entry = by_batch[str(bid)]
        assert entry["d20_report"]["pairable_n"] == 1
        assert entry["matured_cases"] == 1 and entry["unmatured_cases"] == 0
        assert entry["outcome_heads"]["resolved"] == 1
        case_ref = entry["cases"][0]
        assert case_ref["commit_id"] and case_ref["head_revision"] == 1

    # the missed week: planned, no report, NA — inside the denominator
    # (its case window was planned around the past cutoff, so the case
    # is mature but has no outcome head and no commit)
    missed = by_batch[str(f["missed_batch_id"])]
    assert missed["point_estimate"] is None
    assert missed["d20_report"] is None
    assert missed["backfilled_plan"] is True
    assert missed["matured_cases"] == 1
    assert missed["outcome_heads"]["no_head"] == 1
    assert missed["cases"][0]["commit_id"] is None

    # schedule disclosure matches the registered default
    assert content["schedule"]["due_at"] == (
        await monthly_due_at(db_engine, f["month"])
    ).isoformat()


async def test_monthly_summary_na_batches_never_dilute_the_mean(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id, quant_ps=(0.6,))
    # one scorable batch (d = 0.11) + one missed: the mean is that
    # batch's estimate alone, not diluted by the NA batch
    campaign_id = f["ctx"]["campaign"].campaign_id
    result = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert result.content["metrics"]["monthly_mean_paired_brier_delta"] == "0.1100000000"
    assert result.content["metrics"]["scorable_batches"] == 1
    assert result.content["metrics"]["planned_batches"] == 2


# --- readiness and rejection --------------------------------------------------------


async def test_monthly_summary_not_ready_before_month_end(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    current_month = datetime.now(UTC).date().replace(day=1)
    with pytest.raises(NotReady, match="has not ended"):
        await generate_monthly_report(db_engine, ctx["campaign"].campaign_id, current_month)


async def test_monthly_summary_rejects_month_without_batches(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id)
    campaign_id = f["ctx"]["campaign"].campaign_id
    prev = _prev_month(f["month"])
    with pytest.raises(MonthlyError, match="no batch"):
        await generate_monthly_report(db_engine, campaign_id, prev)
    with pytest.raises(MonthlyError, match="first day"):
        await generate_monthly_report(
            db_engine, campaign_id, f["month"] + timedelta(days=15)
        )


# --- scheduler integration --------------------------------------------------------


async def test_tick_generates_monthly_summary_when_due(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id)
    campaign_id = f["ctx"]["campaign"].campaign_id

    tick = await scheduler_tick(db_engine)
    assert {
        "month": f["month"].isoformat(),
        "report_version": 1,
    } in tick["monthly_reports_generated"]

    latest = await latest_monthly_report(db_engine, campaign_id, f["month"])
    assert latest["content"]["metrics"]["monthly_mean_paired_brier_delta"] == "0.0550000000"
    assert latest["content"]["metrics"]["scorable_batches"] == 2

    # the current (unended) month never gets a report; the due month
    # is digest-gated on later ticks
    tick2 = await scheduler_tick(db_engine)
    assert tick2["monthly_reports_skipped"] >= 1
    assert not tick2["monthly_reports_generated"]
    async with db_engine.begin() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM monthly_summary_reports "
                    "WHERE campaign_id = :c"
                ),
                {"c": str(campaign_id)},
            )
        ).scalar_one()
    assert n == 1


# --- versioning --------------------------------------------------------------------


async def test_monthly_summary_idempotent_and_append_only(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id)
    campaign_id = f["ctx"]["campaign"].campaign_id

    r1 = await generate_monthly_report(db_engine, campaign_id, f["month"])
    r2 = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert r1.created and not r2.created
    assert r2.report_version == r1.report_version == 1
    assert r2.content_sha256 == r1.content_sha256

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM monthly_summary_reports "
                    "WHERE campaign_id = :c"
                ),
                {"c": str(campaign_id)},
            )
        ).scalar_one()
    assert rows == 1  # identical state never appends

    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM monthly_summary_reports"))


async def test_monthly_summary_appends_version_on_correction(db_engine, tenant_id):
    f = await _month_fixture(db_engine, tenant_id)
    campaign_id = f["ctx"]["campaign"].campaign_id
    v1 = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert v1.report_version == 1

    # a vendor correction revises one outcome -> batch report v2
    await asyncio_sleep()
    await _ingest_window(
        db_engine, "S0", f["ctx"]["panel"][0], f["dates"],
        default={"open": 100.0, "close": 101.0},
    )
    await resolve_outcome(db_engine, f["d20_ids"][0])  # revision 2
    report2 = await generate_batch_report(db_engine, f["batch_ids"][0], 20)
    assert report2.created and report2.report_version == 2

    v2 = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert v2.created and v2.report_version == 2
    by_batch = {b["batch_id"]: b for b in v2.content["batches"]}
    assert by_batch[str(f["batch_ids"][0])]["d20_report"]["report_version"] == 2
    # v1 keeps its original references
    async with db_engine.begin() as conn:
        v1_row = (
            await conn.execute(
                text(
                    "SELECT content, supersedes_report_id FROM monthly_summary_reports "
                    "WHERE campaign_id = :c AND report_version = 1"
                ),
                {"c": str(campaign_id)},
            )
        ).mappings().one()
    assert v1_row["supersedes_report_id"] is None
    v1_batches = {b["batch_id"]: b for b in v1_row["content"]["batches"]}
    assert v1_batches[str(f["batch_ids"][0])]["d20_report"]["report_version"] == 1
    assert v1_batches[str(f["batch_ids"][0])]["cases"][0]["head_revision"] == 1

    # regenerating the corrected state is idempotent
    v2b = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert not v2b.created and v2b.report_version == 2

    latest = await latest_monthly_report(db_engine, campaign_id, f["month"])
    assert latest["report_version"] == 2
    assert latest["supersedes_report_id"] == str(v1.report_id)
