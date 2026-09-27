"""S04: point-in-time bar queries — the as-of contract.

Acceptance mapped from the plan (S04):
- 回补不能改变旧快照: an as-of query returns the same answer before
  and after later ingests/corrections
- 源时间缺失时保守: usable_at governs when no vendor timestamp exists
- versions: a vendor correction is a new raw object; PIT picks the
  latest usable version per (security, trade_date)
- forward mode must not look into the future; historical_source mode
  is explicitly labeled reconstruction
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from youwei_core.data.pit import InvalidQuery, daily_bars_asof
from youwei_core.data.securities import IdentitySpec, create_security
from youwei_core.data.tiingo import ingest_daily, TiingoClient


def _row(d, close, volume=1000, div=0.0, split=1.0):
    return {
        "date": f"{d}T00:00:00.000Z", "close": close, "high": close + 1.0,
        "low": close - 1.0, "open": close, "volume": volume,
        "adjClose": close, "adjHigh": close + 1.0, "adjLow": close - 1.0,
        "adjOpen": close, "adjVolume": volume, "divCash": div,
        "splitFactor": split,
    }


def _client(bodies: dict) -> TiingoClient:
    def handler(request: httpx.Request) -> httpx.Response:
        ticker = request.url.path.rsplit("/", 2)[-2]
        return httpx.Response(200, text=bodies[ticker])

    return TiingoClient(
        "test-token", base_url="https://mock.test/tiingo",
        transport=httpx.MockTransport(handler),
    )


async def _ingest(engine, bodies, ticker, security_id, start, end):
    client = _client(bodies)
    return await ingest_daily(
        engine, client,
        security_id=security_id, ticker=ticker,
        start_date=start, end_date=end,
    )


async def _security(engine, ticker="AAPL"):
    return await create_security(
        engine, identities=[IdentitySpec("ticker", ticker, date(1990, 1, 1))]
    )


async def test_asof_picks_latest_usable_version(db_engine):
    """A vendor correction is a new version: as-of before it sees the
    original value; as-of after sees the correction."""
    sec = await _security(db_engine)
    start, end = date(2026, 8, 1), date(2026, 8, 31)

    v1 = await _ingest(
        db_engine, {"AAPL": json.dumps([_row("2026-08-10", 308.26)])},
        "AAPL", sec, start, end,
    )
    await asyncio_sleep()
    v2 = await _ingest(
        db_engine, {"AAPL": json.dumps([_row("2026-08-10", 308.30)])},  # corrected close
        "AAPL", sec, start, end,
    )
    assert v1.raw_object_id != v2.raw_object_id
    assert v2.usable_at > v1.usable_at

    before = await daily_bars_asof(
        db_engine, [sec], start, end, as_of=v1.usable_at, mode="forward"
    )
    after = await daily_bars_asof(
        db_engine, [sec], start, end, as_of=v2.usable_at, mode="forward"
    )
    assert len(before) == len(after) == 1
    assert before[0]["close"] == 308.26
    assert before[0]["provenance"]["raw_object_id"] == str(v1.raw_object_id)
    assert after[0]["close"] == 308.30
    assert after[0]["provenance"]["raw_object_id"] == str(v2.raw_object_id)


async def test_asof_before_any_ingest_returns_nothing(db_engine):
    sec = await _security(db_engine)
    v1 = await _ingest(
        db_engine, {"AAPL": json.dumps([_row("2026-08-10", 308.26)])},
        "AAPL", sec, date(2026, 8, 1), date(2026, 8, 31),
    )
    early = v1.usable_at - timedelta(seconds=1)
    bars = await daily_bars_asof(
        db_engine, [sec], date(2026, 8, 1), date(2026, 8, 31),
        as_of=early, mode="forward",
    )
    assert bars == []  # not yet usable: absent, never backfilled


async def test_backfill_does_not_change_old_asof_result(db_engine):
    """The S04 acceptance: 回补不能改变旧快照."""
    sec = await _security(db_engine)
    start, end = date(2026, 8, 1), date(2026, 8, 31)

    v1 = await _ingest(
        db_engine, {"AAPL": json.dumps([_row("2026-08-10", 308.26)])},
        "AAPL", sec, start, end,
    )
    cutoff = v1.usable_at
    snapshot_then = await daily_bars_asof(
        db_engine, [sec], start, end, as_of=cutoff, mode="forward"
    )

    # later: a full-range re-fetch that both CORRECTS the 08-10 close
    # and BACKFILLS 08-07 (new trade dates absent before)
    await asyncio_sleep()
    await _ingest(
        db_engine,
        {"AAPL": json.dumps([_row("2026-08-07", 313.33), _row("2026-08-10", 308.30)])},
        "AAPL", sec, start, end,
    )

    snapshot_again = await daily_bars_asof(
        db_engine, [sec], start, end, as_of=cutoff, mode="forward"
    )
    assert snapshot_then == snapshot_again  # byte-identical answer at the old cutoff
    assert len(snapshot_again) == 1
    assert snapshot_again[0]["close"] == 308.26

    # a NEW cutoff sees the backfill + correction
    latest = await daily_bars_asof(
        db_engine, [sec], start, end,
        as_of=datetime.now(timezone.utc), mode="forward",
    )
    assert len(latest) == 2
    assert {b["trade_date"] for b in latest} == {"2026-08-07", "2026-08-10"}
    assert [b["close"] for b in latest if b["trade_date"] == "2026-08-10"] == [308.30]


async def test_forward_mode_rejects_future_asof(db_engine):
    sec = await _security(db_engine)
    with pytest.raises(InvalidQuery, match="future"):
        await daily_bars_asof(
            db_engine, [sec], date(2026, 8, 1), date(2026, 8, 31),
            as_of=datetime.now(timezone.utc) + timedelta(hours=1), mode="forward",
        )


async def test_historical_source_mode_allows_past_reconstruction(db_engine):
    sec = await _security(db_engine)
    start, end = date(2026, 8, 1), date(2026, 8, 31)
    await _ingest(
        db_engine, {"AAPL": json.dumps([_row("2026-08-10", 308.26)])},
        "AAPL", sec, start, end,
    )
    # historical_source mode is the explicitly labeled reconstruction path
    bars = await daily_bars_asof(
        db_engine, [sec], start, end,
        as_of=datetime.now(timezone.utc), mode="historical_source",
    )
    assert len(bars) == 1


async def test_mode_and_range_validation(db_engine):
    sec = await _security(db_engine)
    with pytest.raises(InvalidQuery):
        await daily_bars_asof(
            db_engine, [sec], date(2026, 8, 1), date(2026, 8, 31),
            as_of=datetime.now(timezone.utc), mode="bogus",
        )
    with pytest.raises(InvalidQuery):
        await daily_bars_asof(
            db_engine, [sec], date(2026, 9, 1), date(2026, 8, 1),
            as_of=datetime.now(timezone.utc), mode="forward",
        )
    assert await daily_bars_asof(
        db_engine, [], date(2026, 8, 1), date(2026, 8, 31),
        as_of=datetime.now(timezone.utc), mode="forward",
    ) == []


async def asyncio_sleep():
    import asyncio

    await asyncio.sleep(0.02)  # separate transactions -> distinct usable_at
