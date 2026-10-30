import pytest
from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot


@pytest.fixture(scope="session")
def calendar():
    return TradingCalendar()


@pytest.fixture
def snapshot(calendar):
    return replay_snapshot(calendar, count=21)
