"""Independent Nasdaq notices, not expectations computed from exchange_calendars."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from yta_alpha.errors import DomainError

REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures/nasdaq-calendar-reference.json").read_text()
)


@pytest.mark.parametrize("day", REFERENCE["closed"])
def test_official_nasdaq_closures_are_not_sessions(calendar, day):
    session = date.fromisoformat(day)
    assert calendar.sessions_between(session, session) == ()
    with pytest.raises(DomainError) as error:
        calendar.close_at(session)
    assert error.value.code == "NOT_TRADING_SESSION"


@pytest.mark.parametrize("reference", REFERENCE["early_close"])
def test_official_nasdaq_early_close_times(calendar, reference):
    session = date.fromisoformat(reference["session"])
    local = calendar.close_at(session).astimezone(ZoneInfo(reference["timezone"]))
    assert local.strftime("%H:%M") == reference["local_close"]
    assert local.date() == session


def test_target_skips_special_closure_and_latest_closed_respects_early_close(calendar):
    assert calendar.after(date(2025, 1, 8), 5) == date(2025, 1, 16)
    assert calendar.after(date(2026, 11, 25), 5) == date(2026, 12, 3)
    assert calendar.latest_closed(datetime(2026, 11, 27, 17, 59, 59, tzinfo=UTC)) == date(
        2026, 11, 25
    )
    assert calendar.latest_closed(datetime(2026, 11, 27, 18, tzinfo=UTC)) == date(2026, 11, 27)
