"""Bounded NVDA regular-session qualification of the explicitly named XNYS proxy.

References: Nasdaq Trader 2024/2025 trading-calendar PDFs, ETA2024-87
(2025-01-09 special closure), and Trader.aspx?id=Calendar (2026 schedule).
No claim is made for another security or dates outside this reviewed window.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from yta_alpha.calendar import TradingCalendar

VERSION = "nvda-nasdaq-20241031-20261231-v1"
START, END = date(2024, 10, 31), date(2026, 12, 31)
CLOSED = set(
    map(
        date.fromisoformat,
        """
2024-11-28 2024-12-25
2025-01-01 2025-01-09 2025-01-20 2025-02-17 2025-04-18 2025-05-26
2025-06-19 2025-07-04 2025-09-01 2025-11-27 2025-12-25
2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19
2026-07-03 2026-09-07 2026-11-26 2026-12-25
""".split(),
    )
)
EARLY = set(
    map(
        date.fromisoformat,
        """
2024-11-29 2024-12-24 2025-07-03 2025-11-28 2025-12-24
2026-11-27 2026-12-24
""".split(),
    )
)


def verify(calendar: TradingCalendar, start: date, end: date) -> str:
    if not START <= start <= end <= END:
        raise ValueError("Calendar qualification does not cover this window")
    expected = []
    day = start
    zone = ZoneInfo("America/New_York")
    while day <= end:
        if day.weekday() < 5 and day not in CLOSED:
            expected.append(day)
            opened, closed = calendar.open_at(day), calendar.close_at(day)
            if opened.astimezone(zone).strftime("%H:%M") != "09:30" or (
                closed.astimezone(zone).strftime("%H:%M") != ("13:00" if day in EARLY else "16:00")
            ):
                raise ValueError("Calendar hours disagree with Nasdaq reference")
        day += timedelta(days=1)
    if tuple(expected) != calendar.sessions_between(start, end):
        raise ValueError("Calendar sessions disagree with Nasdaq reference")
    return VERSION
