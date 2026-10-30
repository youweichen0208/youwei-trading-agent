import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from yta_alpha.acquisition_quality import assess
from yta_alpha.acquisition_store import StoredAcquisition
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.massive import Acquisition, RawPage, normalize_splits
from yta_alpha.models import DataMode


@pytest.fixture(scope="module")
def calendar():
    return TradingCalendar()


def stored(start, end, dates, retrieved, *, split_date=None):
    rows = []
    for day in dates:
        midnight = datetime.combine(
            date.fromisoformat(day), datetime.min.time(), tzinfo=ZoneInfo("America/New_York")
        )
        timestamp = (midnight.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
            milliseconds=1
        )
        rows.append(dict(o=100, h=102, l=99, c=101, v=1000, t=timestamp))
    bars = RawPage("fixture", (), retrieved, json.dumps({"results": rows}).encode())
    splits = (
        []
        if split_date is None
        else [
            {
                "id": "fixture-action",
                "ticker": "NVDA",
                "execution_date": split_date,
                "adjustment_type": "forward_split",
                "split_from": 1,
                "split_to": 2,
            }
        ]
    )
    actions = RawPage("fixture", (), retrieved, json.dumps({"results": splits}).encode())
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    result = Acquisition((bars,), (actions,), normalize_splits([actions], start, end))
    return StoredAcquisition(start, end, DataMode.REPLAY, result)


def test_missing_sessions_are_listed_without_filling_or_backdating(calendar):
    value = stored(
        "2024-11-27", "2024-12-02", ["2024-11-27"], datetime(2024, 12, 2, 22, tzinfo=UTC)
    )
    before = value.acquisition.bars[0].body
    report = assess(value, calendar)
    assert report["missing_sessions"] == ["2024-11-29", "2024-12-02"]
    assert report["expected_completed_session_count"] == 3
    assert report["latest_expected_session"] == "2024-12-02"
    assert report["status"] == "ISSUES_FOUND"
    assert report["qualification_status"] == "UNVERIFIED"
    assert value.acquisition.bars[0].body == before


@pytest.mark.parametrize("minute,premature", [(59, True), (60, False)])
def test_half_day_uses_actual_close_and_does_not_flag_future_missing(calendar, minute, premature):
    at = datetime(2024, 11, 29, 17, tzinfo=UTC) + timedelta(minutes=minute)
    value = stored("2024-11-28", "2024-12-01", ["2024-11-29"], at)
    report = assess(value, calendar)
    assert report["premature_bars"] == (["2024-11-29"] if premature else [])
    assert report["not_yet_due_sessions"] == (["2024-11-29"] if premature else [])
    assert report["missing_sessions"] == []
    assert report["status"] == ("ISSUES_FOUND" if premature else "CHECKS_PASSED")
    assert report["qualification_status"] == "UNVERIFIED"


@pytest.mark.parametrize("day,hour", [("2024-03-08", 21), ("2024-03-11", 20)])
def test_dst_close_boundary(calendar, day, hour):
    close = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=hour
    )
    value = stored(day, day, [day], close)
    assert assess(value, calendar)["premature_bars"] == []
    value = stored(day, day, [day], close - timedelta(microseconds=1))
    assert assess(value, calendar)["premature_bars"] == [day]


def test_weekend_bars_and_actions_are_identified(calendar):
    value = stored(
        "2024-11-29",
        "2024-12-01",
        ["2024-11-29", "2024-11-30"],
        datetime(2024, 12, 2, tzinfo=UTC),
        split_date="2024-11-30",
    )
    report = assess(value, calendar)
    assert report["non_session_bars"] == report["non_session_actions"] == ["2024-11-30"]
    assert report["missing_sessions"] == [] and report["status"] == "ISSUES_FOUND"


def test_weekend_only_window_and_calendar_bounds(calendar):
    value = stored("2024-03-09", "2024-03-10", [], datetime(2024, 3, 11, tzinfo=UTC))
    report = assess(value, calendar)
    assert report["expected_completed_session_count"] == 0
    assert report["latest_expected_session"] is None
    assert report["missing_sessions"] == report["not_yet_due_sessions"] == []
    assert report["calendar_version"] == calendar.version
    assert "PROXY" in report["calendar_basis"]
    with pytest.raises(DomainError) as error:
        calendar.sessions_between(date(1980, 1, 1), date(2024, 1, 1))
    assert error.value.code == "CALENDAR_OUT_OF_RANGE"
    with pytest.raises(DomainError) as error:
        calendar.sessions_between(date(2024, 2, 1), date(2024, 1, 1))
    assert error.value.code == "INVALID_TIME"


def test_later_page_does_not_make_earlier_partial_bar_final(calendar):
    from dataclasses import replace

    first = stored(
        "2024-11-27", "2024-11-29", ["2024-11-27"], datetime(2024, 11, 27, 20, tzinfo=UTC)
    )
    second = stored(
        "2024-11-27", "2024-11-29", ["2024-11-29"], datetime(2024, 11, 29, 19, tzinfo=UTC)
    )
    value = replace(
        first,
        acquisition=replace(
            first.acquisition, bars=first.acquisition.bars + second.acquisition.bars
        ),
    )
    report = assess(value, calendar)
    assert report["missing_sessions"] == []
    assert report["premature_bars"] == ["2024-11-27"]
