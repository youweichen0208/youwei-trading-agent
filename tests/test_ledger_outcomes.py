"""S05b: outcome resolution and append-only revisions.

Acceptance mapped from the plan (S05) and target-spec §2–§5:
- returns follow the registered convention: entry open -> exit close,
  ex-date-close dividend reinvestment (entry-day ex pays nothing,
  exit-day ex still counts), splits adjust units
- resolution uses the PIT view at the database clock and always
  references the frozen snapshot it computed from — recomputable
- incomplete data within the grace period (5 trading days after the
  planned exit) is pending; after it, unresolved with the missing
  pieces — never a fabricated number
- later data or vendor corrections append new revisions superseding
  the head; old revisions never change; forks are impossible
- unscorable is an evidence-backed market fact, never inferred from
  missing vendor data
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from youwei_core.data.calendar import session_times
from youwei_core.ledger.outcomes import (
    InvalidOutcomeState,
    NotYetMature,
    OutcomeError,
    current_outcome,
    record_unscorable,
    resolve_outcome,
    total_return_from_bars,
    verify_outcome_chains,
)
from youwei_core.ledger.service import plan_batch
from youwei_core.data.calendar import next_weekly_cutoff
from test_data_pit import _ingest, _security
from test_ledger_campaign import _setup


def _bar(d, open_, close, div=0.0, split=1.0, volume=1000):
    return {
        "date": f"{d}T00:00:00.000Z",
        "open": open_,
        "high": max(open_, close) + 1.0,
        "low": min(open_, close) - 1.0,
        "close": close,
        "volume": volume,
        "adjClose": close,
        "adjHigh": close + 1.0,
        "adjLow": close - 1.0,
        "adjOpen": open_,
        "adjVolume": volume,
        "divCash": div,
        "splitFactor": split,
    }


async def _window_dates(engine, *, exit_sessions_back, span):
    """(entry_date, exit_date, dates) with `span` trading days ending
    `exit_sessions_back` sessions strictly before today."""
    today = datetime.now(UTC).date()
    async with engine.begin() as conn:
        recent = list(
            (
                await conn.execute(
                    text(
                        "SELECT date FROM calendar_days WHERE venue = 'XNYS' "
                        "AND is_trading AND date < :today ORDER BY date DESC"
                    ),
                    {"today": today},
                )
            ).scalars().all()
        )
    assert len(recent) >= exit_sessions_back + span - 1
    dates_desc = recent[exit_sessions_back - 1 : exit_sessions_back - 1 + span]
    dates = sorted(dates_desc)
    return dates[0], dates[-1], dates


async def _retarget_case(engine, case_id, entry_date, exit_date):
    """Point a planned case at concrete past sessions (test fixture
    via the ops escape hatch)."""
    entry_at = (await session_times(engine, entry_date)).open_utc
    exit_at = (await session_times(engine, exit_date)).close_utc
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE forecast_cases SET entry_at_utc = :e, exit_at_utc = :x "
                "WHERE id = :id"
            ),
            {"e": entry_at, "x": exit_at, "id": str(case_id)},
        )


async def _ingest_window(engine, ticker, sec_id, dates, *, rows=None, default=None):
    """Ingest one bar per trading day of the window."""
    default = default or {}
    rows = rows or [
        _bar(
            d,
            default.get("open", 100.0),
            default.get("close", 100.0),
            div=default.get("div", 0.0),
            split=default.get("split", 1.0),
        )
        for d in dates
    ]
    await _ingest(
        engine, {ticker: json.dumps(rows)}, ticker, sec_id, dates[0], dates[-1]
    )
    return rows


async def _case_for(engine, tenant_id, *, exit_sessions_back, span):
    """Registered campaign + a case retargeted to a concrete window."""
    ctx = await _setup(engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    case_id = plan.case_ids[0]
    entry_date, exit_date, dates = await _window_dates(
        engine, exit_sessions_back=exit_sessions_back, span=span
    )
    await _retarget_case(engine, case_id, entry_date, exit_date)
    return ctx, case_id, dates


# --- the registered return convention ---------------------------------------


async def test_resolved_total_return_with_dividend(db_engine, tenant_id):
    """units = 1/entry_open; ex-date reinvest; exit close valuation."""
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=1, span=20)
    sec, bench = ctx["panel"][0], ctx["benchmark"]

    # security: entry open 100, closes 100, div 10 mid-window, exit close 110
    sec_rows = [ _bar(d, 100.0, 100.0) for d in dates ]
    sec_rows[5]["divCash"] = 10.0
    sec_rows[-1]["close"] = 110.0
    sec_rows[-1]["adjClose"] = 110.0
    await _ingest_window(db_engine, "S0", sec, dates, rows=sec_rows)
    # benchmark: entry open 500, closes 500, exit close 505
    bench_rows = [ _bar(d, 500.0, 500.0) for d in dates ]
    bench_rows[-1]["close"] = 505.0
    bench_rows[-1]["adjClose"] = 505.0
    await _ingest_window(db_engine, "SPY", bench, dates, rows=bench_rows)

    attempt = await resolve_outcome(db_engine, case_id)
    assert attempt.created and attempt.status == "resolved"
    assert attempt.revision == 1
    assert attempt.asset_return == Decimal("0.21")
    assert attempt.benchmark_return == Decimal("0.01")
    assert attempt.excess_return == Decimal("0.20")

    head = await current_outcome(db_engine, case_id)
    assert head["status"] == "resolved"
    assert head["prices_and_actions_snapshot_id"] == str(attempt.snapshot_id)
    assert head["resolver_version"] == "total-return-v1"
    assert head["basis"]["computed_from_snapshot"] is True

    # recomputable from the referenced frozen snapshot
    from youwei_core.data.snapshots import read_snapshot

    frozen = await read_snapshot(db_engine, attempt.snapshot_id)
    bars = json.loads(frozen["content"])
    by_sec = {}
    for bar in bars:
        by_sec.setdefault(bar["security_id"], []).append(bar)
    asset_bars = sorted(by_sec[str(sec)], key=lambda b: b["trade_date"])
    bench_bars = sorted(by_sec[str(bench)], key=lambda b: b["trade_date"])
    assert total_return_from_bars(asset_bars) == attempt.asset_return
    assert total_return_from_bars(bench_bars) == attempt.benchmark_return


async def test_d1_same_day_entry_exit(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=1, span=1)
    sec, bench = ctx["panel"][0], ctx["benchmark"]
    await _ingest_window(db_engine, "S0", sec, dates, default={"open": 100.0, "close": 110.0})
    await _ingest_window(db_engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})

    attempt = await resolve_outcome(db_engine, case_id)
    assert attempt.status == "resolved"
    assert attempt.asset_return == Decimal("0.10")
    assert attempt.benchmark_return == Decimal("0")
    assert attempt.excess_return == Decimal("0.10")


async def test_dividend_entitlement_boundaries(db_engine, tenant_id):
    """Ex-date == entry day pays nothing (bought at that open); ex-date
    == exit day still counts (held to that close). Two different
    securities so the ingests cannot interfere."""
    ctx = await _setup(db_engine, tenant_id, n_panel=2)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # case order: panel-major, horizon-minor -> [S0-D1, S0-D20, S0-D60, S1-D1, ...]
    case_a, case_b = plan.case_ids[0], plan.case_ids[3]
    entry_date, exit_date, dates = await _window_dates(
        db_engine, exit_sessions_back=1, span=3
    )
    await _retarget_case(db_engine, case_a, entry_date, exit_date)
    await _retarget_case(db_engine, case_b, entry_date, exit_date)

    sec_a, sec_b, bench = ctx["panel"][0], ctx["panel"][1], ctx["benchmark"]

    # entry-day dividend: NOT entitled
    rows = [ _bar(d, 100.0, 100.0) for d in dates ]
    rows[0]["divCash"] = 10.0
    rows[-1]["close"] = 110.0
    rows[-1]["adjClose"] = 110.0
    await _ingest_window(db_engine, "S0", sec_a, dates, rows=rows)

    # exit-day dividend: entitled
    rows_b = [ _bar(d, 100.0, 100.0) for d in dates ]
    rows_b[-1]["divCash"] = 10.0
    rows_b[-1]["close"] = 110.0
    rows_b[-1]["adjClose"] = 110.0
    await _ingest_window(db_engine, "S1", sec_b, dates, rows=rows_b)

    await _ingest_window(db_engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})

    attempt = await resolve_outcome(db_engine, case_a)
    assert attempt.asset_return == Decimal("0.10")  # 110/100 - 1, no div

    attempt2 = await resolve_outcome(db_engine, case_b)
    # (1 + 10/110) * 110 / 100 - 1 = 0.2
    assert attempt2.asset_return == Decimal("0.20")


async def test_split_adjusts_units(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=1, span=3)
    sec, bench = ctx["panel"][0], ctx["benchmark"]
    rows = [ _bar(d, 100.0, 100.0) for d in dates ]
    rows[1]["splitFactor"] = 2.0
    rows[1]["close"] = 50.0
    rows[1]["adjClose"] = 50.0
    rows[-1]["close"] = 55.0
    rows[-1]["adjClose"] = 55.0
    await _ingest_window(db_engine, "S0", sec, dates, rows=rows)
    await _ingest_window(db_engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})
    attempt = await resolve_outcome(db_engine, case_id)
    # units 0.01 * 2 = 0.02; wealth = 0.02 * 55 = 1.1
    assert attempt.asset_return == Decimal("0.10")


# --- grace period and missing data ------------------------------------------


async def test_pending_within_grace_writes_nothing(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=1, span=5)
    # benchmark data only; the security is entirely missing
    await _ingest_window(
        db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    attempt = await resolve_outcome(db_engine, case_id)
    assert attempt.status == "pending"
    assert attempt.created is False
    assert attempt.revision is None
    assert attempt.snapshot_id is None  # nothing written, nothing frozen
    assert {m["security"] for m in attempt.missing} == {"security"}
    assert await current_outcome(db_engine, case_id) is None


async def test_unresolved_after_grace_with_missing_detail(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    await _ingest_window(
        db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    attempt = await resolve_outcome(db_engine, case_id)
    assert attempt.status == "unresolved" and attempt.created
    assert attempt.asset_return is None

    head = await current_outcome(db_engine, case_id)
    assert head["status"] == "unresolved"
    assert head["basis"]["missing"]
    assert head["basis"]["grace_deadline_utc"]
    assert head["prices_and_actions_snapshot_id"] == str(attempt.snapshot_id)


async def test_zero_volume_bar_is_not_a_valid_price(db_engine, tenant_id):
    """A phantom zero-volume row never counts as an entry price."""
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=3)
    sec, bench = ctx["panel"][0], ctx["benchmark"]
    rows = [ _bar(d, 100.0, 100.0, volume=0) for d in dates ]  # all phantom
    await _ingest_window(db_engine, "S0", sec, dates, rows=rows)
    await _ingest_window(db_engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})
    attempt = await resolve_outcome(db_engine, case_id)
    assert attempt.status == "unresolved"
    head = await current_outcome(db_engine, case_id)
    assert all(m.get("reason") == "invalid_bar" for m in head["basis"]["missing"])


# --- revisions ----------------------------------------------------------------


async def test_late_data_appends_resolved_revision(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    await _ingest_window(
        db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    first = await resolve_outcome(db_engine, case_id)
    assert first.status == "unresolved"

    # the missing data arrives after the grace period
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 105.0}
    )
    second = await resolve_outcome(db_engine, case_id)
    assert second.status == "resolved" and second.created
    assert second.revision == 2

    head = await current_outcome(db_engine, case_id)
    assert head["revision"] == 2
    assert head["supersedes_outcome_id"] == str(first.outcome_id)
    assert head["correction_reason"] == "data_revision"

    # the old revision is intact
    async with db_engine.begin() as conn:
        old = (
            await conn.execute(
                text(
                    "SELECT status FROM outcome_revisions WHERE id = :id"
                ),
                {"id": str(first.outcome_id)},
            )
        ).scalar_one()
    assert old == "unresolved"


async def test_vendor_correction_appends_new_values(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=20)
    sec, bench = ctx["panel"][0], ctx["benchmark"]
    rows = [ _bar(d, 100.0, 100.0) for d in dates ]
    rows[5]["divCash"] = 10.0
    rows[-1]["close"] = 110.0
    rows[-1]["adjClose"] = 110.0
    await _ingest_window(db_engine, "S0", sec, dates, rows=rows)
    await _ingest_window(db_engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})

    first = await resolve_outcome(db_engine, case_id)
    assert first.asset_return == Decimal("0.21")

    # a vendor correction to the exit close arrives
    corrected = [dict(r) for r in rows]
    corrected[-1]["close"] = 120.0
    corrected[-1]["adjClose"] = 120.0
    await _ingest_window(db_engine, "S0", sec, dates, rows=corrected)

    second = await resolve_outcome(db_engine, case_id, correction_reason="vendor_close_correction")
    assert second.created and second.revision == 2
    assert second.asset_return == Decimal("0.32")  # 0.011 * 120 - 1
    assert second.snapshot_id != first.snapshot_id

    head = await current_outcome(db_engine, case_id)
    assert head["correction_reason"] == "vendor_close_correction"
    assert head["asset_return"] == "0.3200000000"


async def test_idempotent_reresolution(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=1, span=5)
    await _ingest_window(db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 105.0})
    await _ingest_window(db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})

    first = await resolve_outcome(db_engine, case_id)
    again = await resolve_outcome(db_engine, case_id)
    assert first.created and not again.created
    assert again.outcome_id == first.outcome_id
    assert again.revision == 1

    async with db_engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM outcome_revisions"))
        ).scalar_one()
        resolved_events = (
            await conn.execute(
                text("SELECT count(*) FROM events WHERE event_type = 'outcome.resolved'")
            )
        ).scalar_one()
    assert n == 1
    assert resolved_events == 1


async def test_not_yet_mature_rejected(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    with pytest.raises(NotYetMature):
        await resolve_outcome(db_engine, plan.case_ids[0])


# --- unscorable ----------------------------------------------------------------


async def test_unscorable_requires_evidence_and_is_sticky(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    with pytest.raises(OutcomeError):
        await record_unscorable(db_engine, case_id, reason="", evidence_ref="x")

    first = await record_unscorable(
        db_engine, case_id, reason="halted_at_entry", evidence_ref="exchange-notice-123"
    )
    assert first.status == "unscorable" and first.revision == 1

    again = await record_unscorable(
        db_engine, case_id, reason="halted_at_entry", evidence_ref="exchange-notice-123"
    )
    assert not again.created

    # resolution refuses to silently overwrite the market fact
    with pytest.raises(InvalidOutcomeState):
        await resolve_outcome(db_engine, case_id)

    head = await current_outcome(db_engine, case_id)
    assert head["basis"]["evidence_ref"] == "exchange-notice-123"


# --- constraints and the chain -------------------------------------------------


async def test_resolved_requires_values_at_db_level(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO outcome_revisions (id, case_id, revision, status, "
                    "entry_at_utc, exit_at_utc, resolver_version) VALUES "
                    "(:id, :c, 1, 'resolved', now(), now(), 'x')"
                ),
                {"id": str(uuid.uuid4()), "c": str(case_id)},
            )


async def test_outcome_revisions_append_only(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    await record_unscorable(db_engine, case_id, reason="halted_at_entry", evidence_ref="n")
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("UPDATE outcome_revisions SET status = 'resolved'"))
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM outcome_revisions"))


async def test_chain_verifier_and_tampering(db_engine, tenant_id):
    ctx, case_id, dates = await _case_for(db_engine, tenant_id, exit_sessions_back=10, span=5)
    await _ingest_window(
        db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    r1 = await resolve_outcome(db_engine, case_id)  # unresolved
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 105.0}
    )
    r2 = await resolve_outcome(db_engine, case_id)  # resolved, revision 2

    report = await verify_outcome_chains(db_engine)
    assert report["ok"], report["issues"]

    # tamper with the chain via the ops escape hatch: revision 2
    # points at itself instead of the head
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text("UPDATE outcome_revisions SET supersedes_outcome_id = id WHERE id = :id"),
            {"id": str(r2.outcome_id)},
        )
    report = await verify_outcome_chains(db_engine)
    assert not report["ok"]
    assert any("supersedes" in i for i in report["issues"])
