"""S04b: NYSE trading calendar — rule engine (validated against known
exchange facts), versioned builds, session/batch time resolution with
DST, and the calendar-vs-bars crosscheck."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from youwei_core.data.calendar import (
    CalendarError,
    CalendarNotBuilt,
    REGULAR_OPEN,
    build_calendar,
    clock_skew_seconds,
    crosscheck_calendar_against_bars,
    easter,
    generate_calendar_days,
    is_trading_day,
    next_trading_day,
    next_weekly_cutoff,
    nyse_holidays,
    resolve_batch_times,
    session_times,
    trading_day_offset,
)

# --- rule engine (known-facts validation, independent of our code) ----


def test_easter_known_dates():
    assert easter(2026) == date(2026, 4, 5)
    assert easter(2025) == date(2025, 4, 20)
    assert easter(2024) == date(2024, 3, 31)


def test_2026_holidays_match_nyse_schedule():
    h = nyse_holidays(2026)
    assert h[date(2026, 1, 1)] == "New Year's Day"  # Thursday
    assert h[date(2026, 1, 19)] == "Martin Luther King Jr. Day"  # 3rd Mon
    assert h[date(2026, 2, 16)] == "Washington's Birthday"
    assert h[date(2026, 4, 3)] == "Good Friday"  # Easter Apr 5
    assert h[date(2026, 5, 25)] == "Memorial Day"  # last Mon
    assert h[date(2026, 6, 19)] == "Juneteenth"  # Friday
    assert h[date(2026, 7, 3)] == "Independence Day (observed)"  # Jul 4 = Sat
    assert h[date(2026, 9, 7)] == "Labor Day"  # 1st Mon
    assert h[date(2026, 11, 26)] == "Thanksgiving Day"  # 4th Thu
    assert h[date(2026, 12, 25)] == "Christmas Day"  # Friday
    assert len(h) == 10


def test_new_year_saturday_is_not_observed_on_prior_friday():
    # Jan 1, 2022 was a Saturday: Dec 31, 2021 stayed open (yearly
    # accounting exception), and no 2022 observance either.
    assert date(2021, 12, 31) not in nyse_holidays(2021)
    assert date(2022, 1, 1) not in nyse_holidays(2022)


def test_weekend_holiday_observance_rules():
    # Jul 4, 2020 Saturday -> Friday Jul 3 observed closed
    assert nyse_holidays(2020)[date(2020, 7, 3)] == "Independence Day (observed)"
    # Jun 19, 2022 Sunday -> Monday Jun 20 observed
    assert nyse_holidays(2022)[date(2022, 6, 20)] == "Juneteenth (observed)"
    # Dec 25, 2021 Saturday -> Friday Dec 24 observed (so no early close)
    assert nyse_holidays(2021)[date(2021, 12, 24)] == "Christmas Day (observed)"


def test_juneteenth_only_from_2022():
    assert date(2021, 6, 18) not in nyse_holidays(2021)  # Fri before, normal day
    assert date(2021, 6, 21) not in nyse_holidays(2021)


def test_early_closes():
    days = {(d["date"], d["early_close"]): d for d in generate_calendar_days(2026, 2026)}
    by_date = {d["date"]: d for d in generate_calendar_days(2026, 2026)}
    assert by_date["2026-11-27"]["early_close"] is True  # Friday after Thanksgiving
    assert by_date["2026-12-24"]["early_close"] is True  # Christmas Eve Thursday
    assert by_date["2026-12-23"]["early_close"] is False
    # 2021: Dec 24 was the observed holiday -> closed, not early close
    by_date_2021 = {d["date"]: d for d in generate_calendar_days(2021, 2021)}
    assert by_date_2021["2021-12-24"]["is_trading"] is False


def test_special_closures_recorded():
    by_date = {d["date"]: d for d in generate_calendar_days(2025, 2025)}
    carter = by_date["2025-01-09"]
    assert carter["is_trading"] is False
    assert "Jimmy Carter" in carter["note"]


def test_generation_covers_every_weekday():
    days = generate_calendar_days(2026, 2026)
    dates = [date.fromisoformat(d["date"]) for d in days]
    assert all(d.weekday() < 5 for d in dates)
    assert date(2026, 9, 26) not in dates  # Saturday implied absent
    assert date(2026, 9, 28) in dates  # Monday
    # every weekday of the year is present exactly once
    expected = {
        date(2026, 1, 1) + timedelta(days=i)
        for i in range((date(2026, 12, 31) - date(2026, 1, 1)).days + 1)
        if (date(2026, 1, 1) + timedelta(days=i)).weekday() < 5
    }
    assert set(dates) == expected


# --- storage & queries ------------------------------------------------------


async def test_independence_eve_regular_session_closes_at_13_et(db_engine):
    # NYSE official 2028 calendar: Monday July 3 closes at 13:00 ET.
    # https://www.nyse.com/trade/hours-calendars
    await build_calendar(db_engine, year_start=2028, year_end=2028)
    session = await session_times(db_engine, date(2028, 7, 3))
    assert session.early_close is True
    assert session.close_utc == datetime(2028, 7, 3, 17, tzinfo=UTC)


def test_calendar_records_the_timezone_bytes_it_actually_uses():
    from youwei_core.data.calendar import timezone_provenance
    record = timezone_provenance()
    assert record['source'] == 'tzdata-package'
    assert record['zone'] == 'America/New_York'
    assert record['iana_version'] != 'system-unknown'
    assert len(record['content_sha256']) == 64


async def test_build_is_idempotent_and_refuses_content_change(db_engine):
    b1 = await build_calendar(db_engine, year_start=2024, year_end=2027)
    b2 = await build_calendar(db_engine, year_start=2024, year_end=2027)
    assert b1.version == b2.version
    assert b1.content_sha256 == b2.content_sha256

    # same version with different rules must not silently rewrite
    with pytest.raises(CalendarError, match="bump the version"):
        await build_calendar(
            db_engine, year_start=2024, year_end=2027,
            version=b1.version,
            special_closures={date(2026, 3, 2): "test closure"},
        )


async def test_is_trading_day_and_weekend_semantics(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    assert await is_trading_day(db_engine, date(2026, 9, 25)) is True  # Friday
    assert await is_trading_day(db_engine, date(2026, 9, 26)) is False  # Saturday
    assert await is_trading_day(db_engine, date(2026, 9, 27)) is False  # Sunday
    assert await is_trading_day(db_engine, date(2026, 7, 3)) is False  # observed holiday


async def test_unbuilt_range_raises_not_guesses(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    with pytest.raises(CalendarNotBuilt):
        await is_trading_day(db_engine, date(2030, 6, 10))  # weekday, never built
    with pytest.raises(CalendarNotBuilt):
        await next_trading_day(db_engine, date(2026, 12, 31))  # beyond build


async def test_next_trading_day_skips_weekend_and_holidays(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    # Friday Sep 25 -> Monday Sep 28
    assert await next_trading_day(db_engine, date(2026, 9, 25)) == date(2026, 9, 28)
    # Friday Jul 3 is a holiday: Thursday Jul 2 -> Monday Jul 6
    assert await next_trading_day(db_engine, date(2026, 7, 2)) == date(2026, 7, 6)
    # Wednesday Dec 23 -> Thursday Dec 24 (early close still a trading day)
    assert await next_trading_day(db_engine, date(2026, 12, 23)) == date(2026, 12, 24)
    # Thursday Dec 24 -> Monday Dec 28 (25th holiday, 26/27 weekend)
    assert await next_trading_day(db_engine, date(2026, 12, 24)) == date(2026, 12, 28)


async def test_trading_day_offset_d_horizons(db_engine):
    """TargetSpec §2: entry day counts as D1."""
    await build_calendar(db_engine, year_start=2026, year_end=2027)
    entry = date(2026, 9, 28)
    assert await trading_day_offset(db_engine, entry, 1) == entry
    assert await trading_day_offset(db_engine, entry, 20) == date(2026, 10, 23)
    assert await trading_day_offset(db_engine, entry, 60) == date(2026, 12, 21)

    with pytest.raises(CalendarError):
        await trading_day_offset(db_engine, date(2026, 9, 26), 1)  # Saturday entry


# --- session times & DST -----------------------------------------------------


async def test_session_times_dst_aware(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    # Sep 28 during EDT: 09:30 ET == 13:30 UTC
    s = await session_times(db_engine, date(2026, 9, 28))
    assert s.open_utc.isoformat() == "2026-09-28T13:30:00+00:00"
    assert s.close_utc.isoformat() == "2026-09-28T20:00:00+00:00"
    assert s.early_close is False

    # DST ends Nov 1 2026: Nov 2 is EST, 09:30 ET == 14:30 UTC
    s_nov = await session_times(db_engine, date(2026, 11, 2))
    assert s_nov.open_utc.isoformat() == "2026-11-02T14:30:00+00:00"

    # early close day: Nov 27 (EST) closes 13:00 ET == 18:00 UTC
    s_early = await session_times(db_engine, date(2026, 11, 27))
    assert s_early.early_close is True
    assert s_early.close_utc.isoformat() == "2026-11-27T18:00:00+00:00"

    with pytest.raises(CalendarError):
        await session_times(db_engine, date(2026, 7, 3))  # holiday


# --- batch time resolution (time-protocol §1) --------------------------------


async def test_resolve_batch_times_normal_week(db_engine):
    from zoneinfo import ZoneInfo

    await build_calendar(db_engine, year_start=2026, year_end=2026)
    cutoff = datetime(2026, 9, 26, 6, 0, tzinfo=ZoneInfo("America/New_York"))
    bt = await resolve_batch_times(db_engine, cutoff)
    assert bt.entry_date == date(2026, 9, 28)  # Monday after Saturday cutoff
    assert bt.entry_at_utc.isoformat() == "2026-09-28T13:30:00+00:00"  # EDT
    assert bt.prediction_deadline_utc.isoformat() == "2026-09-28T13:15:00+00:00"
    assert bt.calendar_version.startswith("xnys-")
    assert len(bt.calendar_sha256) == 64
    m = bt.manifest()
    assert m["entry_date"] == "2026-09-28"
    assert m["calendar_version"] == bt.calendar_version


async def test_resolve_batch_times_monday_holiday_pushes_entry(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    # cutoff Sat Jan 17, 2026 -> Mon Jan 19 is MLK -> entry Tue Jan 20
    from zoneinfo import ZoneInfo

    cutoff = datetime(2026, 1, 17, 6, 0, tzinfo=ZoneInfo("America/New_York"))
    bt = await resolve_batch_times(db_engine, cutoff)
    assert bt.entry_date == date(2026, 1, 20)
    assert bt.prediction_deadline_utc.isoformat() == "2026-01-20T14:15:00+00:00"  # EST


async def test_resolve_batch_times_rejects_non_saturday_cutoff(db_engine):
    await build_calendar(db_engine, year_start=2026, year_end=2026)
    from zoneinfo import ZoneInfo

    with pytest.raises(CalendarError, match="Saturday"):
        await resolve_batch_times(
            db_engine, datetime(2026, 9, 25, 6, 0, tzinfo=ZoneInfo("America/New_York"))
        )
    with pytest.raises(CalendarError, match="06:00"):
        await resolve_batch_times(
            db_engine, datetime(2026, 9, 26, 7, 0, tzinfo=ZoneInfo("America/New_York"))
        )


def test_next_weekly_cutoff():
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    # Friday Sep 25, 2026 15:00 ET -> cutoff Sat Sep 26 06:00 ET
    after = datetime(2026, 9, 25, 15, 0, tzinfo=et)
    cutoff = next_weekly_cutoff(after)
    assert cutoff.astimezone(et).isoformat() == "2026-09-26T06:00:00-04:00"
    # Saturday 07:00 ET (past cutoff) -> NEXT Saturday
    after2 = datetime(2026, 9, 26, 7, 0, tzinfo=et)
    assert next_weekly_cutoff(after2).astimezone(et).date() == date(2026, 10, 3)


async def test_clock_skew_small(db_engine):
    skew = await clock_skew_seconds(db_engine)
    assert skew < 5.0  # app and test-db clocks on the same host


# --- calendar vs observed bars crosscheck --------------------------------------


async def test_crosscheck_calendar_against_bars(db_engine):
    from test_data_pit import _client, _row, _security
    from youwei_core.data.tiingo import ingest_daily

    await build_calendar(db_engine, year_start=2026, year_end=2026)
    sec = await _security(db_engine)
    client = _client(
        {
            "AAPL": __import__("json").dumps(
                [
                    _row("2026-09-21", 338.98),
                    _row("2026-09-22", 339.75),
                    _row("2026-09-23", 337.02),
                    _row("2026-09-24", 335.92),
                    _row("2026-09-25", 341.07),
                ]
            )
        }
    )
    ingest = await ingest_daily(
        db_engine, client,
        security_id=sec, ticker="AAPL",
        start_date=date(2026, 9, 21), end_date=date(2026, 9, 25),
    )
    from youwei_core.data.pit import daily_bars_asof

    # as_of from the ingest result (db clock), not app now(): the
    # forward mode's future check compares against the db clock and
    # app-vs-container skew can flip the comparison under load
    bars = await daily_bars_asof(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )
    report = await crosscheck_calendar_against_bars(db_engine, bars)
    assert report["securities_checked"] == 1
    assert report["bars_on_closed_days"] == []
    assert report["missing_on_trading_days"] == []

    # a bar landing on a holiday is a hard discrepancy
    bad = bars + [
        {
            "security_id": str(sec), "trade_date": "2026-07-03",
            "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
            "adj_close": 1, "div_cash": 0, "split_factor": 1, "quality": "ok",
            "provenance": {"raw_object_id": "x", "usable_at": "x",
                           "source_available_at": None, "ingested_at": "x",
                           "source_available_basis": "x"},
        }
    ]
    report2 = await crosscheck_calendar_against_bars(db_engine, bad)
    assert len(report2["bars_on_closed_days"]) == 1
    assert report2["bars_on_closed_days"][0]["date"] == "2026-07-03"
