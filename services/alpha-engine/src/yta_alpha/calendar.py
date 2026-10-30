from datetime import UTC, date, datetime
from importlib.metadata import version

import exchange_calendars as xcals
import pandas as pd

from yta_alpha.errors import DomainError


class TradingCalendar:
    """XNYS regular-session proxy for MVP NVDA; full Nasdaq qualification is pending.

    Official Nasdaq exception-date regression covers only its documented fixture scope.
    This is deliberately not a general multi-exchange resolver. Adding other securities
    requires TASK-118 and exchange-specific calendar verification.
    """

    def __init__(self, start: str = "1990-01-01", end: str = "2035-12-31"):
        self._calendar = xcals.get_calendar("XNYS", start=start, end=end)
        self.version = f"exchange-calendars-{version('exchange-calendars')}:XNYS"

    def _label(self, session: date) -> pd.Timestamp:
        label = pd.Timestamp(session)
        if not self._calendar.first_session <= label <= self._calendar.last_session:
            raise DomainError("CALENDAR_OUT_OF_RANGE", "Calendar coverage is unavailable")
        if not self._calendar.is_session(label):
            raise DomainError("NOT_TRADING_SESSION", "Date is not an exchange session")
        return label

    def open_at(self, session: date) -> datetime:
        return self._calendar.session_open(self._label(session)).to_pydatetime()

    def close_at(self, session: date) -> datetime:
        return self._calendar.session_close(self._label(session)).to_pydatetime()

    def after(self, base: date, count: int) -> date:
        try:
            return self._calendar.session_offset(self._label(base), count).date()
        except (ValueError, IndexError) as error:
            raise DomainError(
                "CALENDAR_OUT_OF_RANGE", "Target exceeds calendar coverage"
            ) from error

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        self._label(start)
        self._label(end)
        return tuple(s.date() for s in self._calendar.sessions_in_range(start, end))

    def sessions_between(self, start: date, end: date) -> tuple[date, ...]:
        """Inclusive query range whose boundaries may be holidays or weekends."""
        if start > end:
            raise DomainError("INVALID_TIME", "Calendar range is reversed")
        if not (
            self._calendar.first_session.date() <= start
            and end <= self._calendar.last_session.date()
        ):
            raise DomainError("CALENDAR_OUT_OF_RANGE", "Calendar coverage is unavailable")
        return tuple(s.date() for s in self._calendar.sessions_in_range(start, end))

    def latest_closed(self, at: datetime) -> date:
        if at.tzinfo is None:
            raise DomainError("INVALID_TIME", "Clock must include a timezone")
        day = at.astimezone(UTC).date()
        if not self._calendar.first_session.date() <= day <= self._calendar.last_session.date():
            raise DomainError("CALENDAR_OUT_OF_RANGE", "Clock exceeds calendar coverage")
        session = self._calendar.date_to_session(pd.Timestamp(day), direction="previous").date()
        if self.close_at(session) > at:
            session = self.after(session, -1)
        return session
