"""NYSE trading calendar (time-protocol.v1).

Rule-based generation (nyse-rules-v1) following current NYSE practice:
- weekends closed
- fixed holidays: New Year's Day, Juneteenth (2022+), Independence
  Day, Christmas. A Saturday holiday is observed the preceding Friday
  EXCEPT New Year's Day (yearly-accounting exception: the prior Dec 31
  stays open); a Sunday holiday is observed the following Monday
- movable: MLK (3rd Mon Jan), Presidents (3rd Mon Feb), Good Friday,
  Memorial (last Mon May), Labor (1st Mon Sep), Thanksgiving (4th Thu
  Nov)
- special closures (hurricanes, national days of mourning) recorded
  explicitly in SPECIAL_CLOSURES and carried into the build
- early closes 13:00 ET: the Friday after Thanksgiving and Dec 24
  when it is a trading day

Storage model: builds are append-only and content-hashed (sealed cases
keep their planned calendar via the version+hash recorded in their
manifests); calendar_days is a rebuildable materialized index holding
one row per WEEKDAY — a weekday without a row means the range was
never built, which queries report as an error, never as a holiday
guess.

Local times resolve through zoneinfo America/New_York (DST-aware);
UTC is the storage format per time-protocol §1.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import calendar_builds, calendar_days

RULES_VERSION = "nyse-rules-v1"
VENUE = "XNYS"
ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE_AT = time(13, 0)

# minute buffer between prediction deadline and entry (time-protocol §1)
DEADLINE_BUFFER = timedelta(minutes=15)

# Known irregular full-day closures inside the relevant history window.
# Rule changes or new events require a new build version — never a
# silent edit of an existing one.
SPECIAL_CLOSURES: dict[date, str] = {
    date(2012, 10, 29): "Hurricane Sandy",
    date(2012, 10, 30): "Hurricane Sandy",
    date(2018, 12, 5): "National Day of Mourning (George H. W. Bush)",
    date(2025, 1, 9): "National Day of Mourning (Jimmy Carter)",
}

JUNETEENTH_FIRST_YEAR = 2022


class CalendarError(Exception):
    pass


class CalendarNotBuilt(CalendarError):
    """The requested date range was never built: refusing to guess."""


# --- rule engine -----------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of the month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """Anonymous Gregorian algorithm (valid for the Gregorian calendar)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, pday = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, pday + 1)


def _observed(holiday: date, name: str) -> dict[date, str]:
    """Weekend observance rules: Saturday -> preceding Friday (except
    New Year), Sunday -> following Monday."""
    if holiday.weekday() == 5:  # Saturday
        if holiday.month == 1 and holiday.day == 1:
            return {}  # yearly-accounting exception: no observance
        return {holiday - timedelta(days=1): f"{name} (observed)"}
    if holiday.weekday() == 6:  # Sunday
        return {holiday + timedelta(days=1): f"{name} (observed)"}
    return {holiday: name}


def nyse_holidays(year: int) -> dict[date, str]:
    holidays: dict[date, str] = {}
    holidays.update(_observed(date(year, 1, 1), "New Year's Day"))
    holidays.update(
        _observed(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    )
    holidays.update(_observed(_nth_weekday(year, 2, 0, 3), "Washington's Birthday"))
    holidays.update(_observed(easter(year) - timedelta(days=2), "Good Friday"))
    holidays.update(_observed(_last_weekday(year, 5, 0), "Memorial Day"))
    if year >= JUNETEENTH_FIRST_YEAR:
        holidays.update(_observed(date(year, 6, 19), "Juneteenth"))
    holidays.update(_observed(date(year, 7, 4), "Independence Day"))
    holidays.update(_observed(_nth_weekday(year, 9, 0, 1), "Labor Day"))
    holidays.update(_observed(_nth_weekday(year, 11, 3, 4), "Thanksgiving Day"))
    holidays.update(_observed(date(year, 12, 25), "Christmas Day"))
    return holidays


def _is_early_close(d: date, holidays: dict[date, str]) -> bool:
    # Friday after Thanksgiving
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    # Christmas Eve when it is a trading day
    if d.month == 12 and d.day == 24 and d not in holidays and d.weekday() < 5:
        return True
    return False


def generate_calendar_days(
    year_start: int, year_end: int, *, special_closures: dict[date, str] | None = None
) -> list[dict]:
    """All WEEKDAYS in [year_start, year_end] with trading status."""
    closures = dict(SPECIAL_CLOSURES)
    if special_closures:
        closures.update(special_closures)

    days = []
    d = date(year_start, 1, 1)
    end = date(year_end, 12, 31)
    while d <= end:
        if d.weekday() < 5:  # weekdays only; weekends implied absent
            holidays = nyse_holidays(d.year)
            if d in closures:
                days.append(
                    {"date": d.isoformat(), "is_trading": False,
                     "early_close": False, "note": closures[d]}
                )
            elif d in holidays:
                days.append(
                    {"date": d.isoformat(), "is_trading": False,
                     "early_close": False, "note": holidays[d]}
                )
            else:
                days.append(
                    {"date": d.isoformat(), "is_trading": True,
                     "early_close": _is_early_close(d, holidays), "note": None}
                )
        d += timedelta(days=1)
    return days


def tzdb_version() -> str:
    """Best-effort IANA version (deployment should pin the tzdata
    package; recorded in manifests per time-protocol §1)."""
    try:
        import tzdata  # type: ignore

        return getattr(tzdata, "IANA_VERSION", "system-unknown")
    except ImportError:
        return "system-unknown"


# --- storage ---------------------------------------------------------------


@dataclass
class CalendarBuild:
    version: str
    content_sha256: str
    day_count: int


async def build_calendar(
    engine: AsyncEngine,
    *,
    year_start: int,
    year_end: int,
    version: str | None = None,
    special_closures: dict[date, str] | None = None,
) -> CalendarBuild:
    """Generate, hash and materialize the calendar. Idempotent for the
    same version; refuses to change an existing version's content."""
    if year_start > year_end:
        raise CalendarError("year_start after year_end")
    version = version or f"xnys-{year_start}-{year_end}-{RULES_VERSION}"
    days = generate_calendar_days(year_start, year_end, special_closures=special_closures)
    content = json.dumps(days, sort_keys=True, separators=(",", ":"))
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(calendar_builds).where(calendar_builds.c.version == version)
            )
        ).mappings().first()
        if existing is not None:
            if existing.content_sha256 != content_sha:
                raise CalendarError(
                    f"version {version!r} already exists with different content; "
                    "bump the version instead of silently changing it"
                )
            day_count = existing.day_count
        else:
            day_count = len(days)
            await conn.execute(
                calendar_builds.insert().values(
                    version=version,
                    venue=VENUE,
                    year_start=year_start,
                    year_end=year_end,
                    rules_version=RULES_VERSION,
                    content=content,
                    content_sha256=content_sha,
                    day_count=day_count,
                    special_closures=[
                        {"date": d.isoformat(), "reason": r}
                        for d, r in sorted((special_closures or {}).items())
                    ],
                )
            )

        for day in days:
            await conn.execute(
                pg_insert(calendar_days)
                .values(
                    venue=VENUE,
                    date=date.fromisoformat(day["date"]),
                    is_trading=day["is_trading"],
                    early_close=day["early_close"],
                    note=day["note"],
                    build_version=version,
                )
                .on_conflict_do_update(
                    index_elements=[calendar_days.c.venue, calendar_days.c.date],
                    set_={
                        "is_trading": day["is_trading"],
                        "early_close": day["early_close"],
                        "note": day["note"],
                        "build_version": version,
                    },
                )
            )
    return CalendarBuild(version=version, content_sha256=content_sha, day_count=day_count)


# --- queries -----------------------------------------------------------------


async def _day_row(conn, d: date, venue: str = VENUE):
    return (
        await conn.execute(
            select(calendar_days).where(
                calendar_days.c.venue == venue, calendar_days.c.date == d
            )
        )
    ).mappings().first()


async def is_trading_day(engine: AsyncEngine, d: date, *, venue: str = VENUE) -> bool:
    if d.weekday() >= 5:
        return False
    async with engine.begin() as conn:
        row = await _day_row(conn, d, venue)
    if row is None:
        raise CalendarNotBuilt(f"{d} is outside the built calendar range")
    return bool(row.is_trading)


async def next_trading_day(
    engine: AsyncEngine, d: date, *, n: int = 1, venue: str = VENUE
) -> date:
    """The n-th trading day strictly after d."""
    if n < 1:
        raise CalendarError("n must be >= 1")
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                select(calendar_days.c.date)
                .where(
                    calendar_days.c.venue == venue,
                    calendar_days.c.is_trading.is_(True),
                    calendar_days.c.date > d,
                )
                .order_by(calendar_days.c.date)
                .limit(n)
            )
        ).scalars().all()
    if len(rows) < n:
        raise CalendarNotBuilt(f"fewer than {n} trading days built after {d}")
    return rows[n - 1]


async def trading_day_offset(
    engine: AsyncEngine, entry: date, n: int, *, venue: str = VENUE
) -> date:
    """TargetSpec §2: entry day counts as D1 — the n-th trading day
    counting entry itself as the first."""
    if n < 1:
        raise CalendarError("n must be >= 1")
    if not await is_trading_day(engine, entry, venue=venue):
        raise CalendarError(f"entry {entry} is not a trading day")
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                select(calendar_days.c.date)
                .where(
                    calendar_days.c.venue == venue,
                    calendar_days.c.is_trading.is_(True),
                    calendar_days.c.date >= entry,
                )
                .order_by(calendar_days.c.date)
                .limit(n)
            )
        ).scalars().all()
    if len(rows) < n:
        raise CalendarNotBuilt(f"fewer than {n} trading days built from {entry}")
    return rows[n - 1]


@dataclass
class SessionTimes:
    date: date
    open_utc: datetime
    close_utc: datetime
    early_close: bool
    build_version: str


async def session_times(
    engine: AsyncEngine, d: date, *, venue: str = VENUE
) -> SessionTimes:
    """Regular session open/close in UTC (DST-aware via zoneinfo)."""
    async with engine.begin() as conn:
        row = await _day_row(conn, d, venue)
    if row is None:
        raise CalendarNotBuilt(f"{d} is outside the built calendar range")
    if not row.is_trading:
        raise CalendarError(f"{d} is not a trading day ({row.note})")
    close_local = EARLY_CLOSE_AT if row.early_close else REGULAR_CLOSE
    open_utc = (
        datetime.combine(d, REGULAR_OPEN, tzinfo=ET).astimezone(UTC)
    )
    close_utc = datetime.combine(d, close_local, tzinfo=ET).astimezone(UTC)
    return SessionTimes(
        date=d,
        open_utc=open_utc,
        close_utc=close_utc,
        early_close=bool(row.early_close),
        build_version=row.build_version,
    )


@dataclass
class BatchTimes:
    """time-protocol §1: the full weekly batch time set, manifest-ready."""

    decision_cutoff_utc: datetime
    decision_cutoff_local: str
    entry_date: date
    entry_at_utc: datetime
    entry_at_local: str
    prediction_deadline_utc: datetime
    prediction_deadline_local: str
    calendar_version: str
    calendar_sha256: str
    tzdb: str

    def manifest(self) -> dict:
        return {
            "decision_cutoff_utc": self.decision_cutoff_utc.isoformat(),
            "decision_cutoff_local": self.decision_cutoff_local,
            "entry_date": self.entry_date.isoformat(),
            "entry_at_utc": self.entry_at_utc.isoformat(),
            "entry_at_local": self.entry_at_local,
            "prediction_deadline_utc": self.prediction_deadline_utc.isoformat(),
            "prediction_deadline_local": self.prediction_deadline_local,
            "calendar_version": self.calendar_version,
            "calendar_sha256": self.calendar_sha256,
            "tzdb_version": self.tzdb,
        }


async def resolve_batch_times(
    engine: AsyncEngine, decision_cutoff_et: datetime
) -> BatchTimes:
    """Resolve the weekly batch times from the Saturday 06:00 ET
    decision cutoff. Entry is the first regular trading day after the
    cutoff; the deadline is 15 minutes before the regular open."""
    if decision_cutoff_et.tzinfo is None:
        raise CalendarError("decision_cutoff must be timezone-aware ET")
    local = decision_cutoff_et.astimezone(ET)
    if local.weekday() != 5 or local.time() != time(6, 0):
        raise CalendarError(
            "decision_cutoff must be Saturday 06:00:00 ET per time-protocol-v1, "
            f"got {local.isoformat()}"
        )

    entry_date = await next_trading_day(engine, local.date())
    session = await session_times(engine, entry_date)

    build = (
        await _build_for(engine, session.build_version)
    )
    deadline = session.open_utc - DEADLINE_BUFFER
    return BatchTimes(
        decision_cutoff_utc=decision_cutoff_et.astimezone(UTC),
        decision_cutoff_local=local.isoformat(),
        entry_date=entry_date,
        entry_at_utc=session.open_utc,
        entry_at_local=datetime.combine(
            entry_date, REGULAR_OPEN, tzinfo=ET
        ).isoformat(),
        prediction_deadline_utc=deadline,
        prediction_deadline_local=deadline.astimezone(ET).isoformat(),
        calendar_version=build.version,
        calendar_sha256=build.content_sha256,
        tzdb=tzdb_version(),
    )


async def _build_for(engine: AsyncEngine, version: str) -> CalendarBuild:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(calendar_builds).where(calendar_builds.c.version == version)
            )
        ).mappings().one()
    return CalendarBuild(
        version=row.version, content_sha256=row.content_sha256, day_count=row.day_count
    )


def next_weekly_cutoff(after_utc: datetime) -> datetime:
    """The first Saturday 06:00 ET strictly after the given instant
    (scheduler helper: batches anchor on it)."""
    now_et = after_utc.astimezone(ET)
    d = now_et.date()
    days_ahead = (5 - d.weekday()) % 7  # 5 = Saturday
    candidate = d + timedelta(days=days_ahead)
    cutoff = datetime.combine(candidate, time(6, 0), tzinfo=ET)
    if cutoff <= now_et:
        cutoff += timedelta(days=7)
    return cutoff.astimezone(UTC)


async def clock_skew_seconds(engine: AsyncEngine) -> float:
    """|app clock - database clock| in seconds. Sealing must stop when
    this exceeds the configured threshold (time-protocol §5)."""
    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()
    return abs((datetime.now(UTC) - db_now).total_seconds())


async def crosscheck_calendar_against_bars(
    engine: AsyncEngine,
    bars: list[dict],
    *,
    venue: str = VENUE,
) -> dict:
    """Compare observed daily bars with the calendar. A bar on a
    non-trading day is a hard discrepancy; a missing bar on a trading
    day after the security's first observation is reported (possible
    suspension — feeds the per-security 停牌校验)."""
    by_security: dict[str, set[date]] = {}
    for bar in bars:
        by_security.setdefault(bar["security_id"], set()).add(
            date.fromisoformat(bar["trade_date"])
        )

    bars_on_closed_days = []
    missing_on_trading_days = []
    async with engine.begin() as conn:
        for sec_id, dates in sorted(by_security.items()):
            for d in sorted(dates):
                row = await _day_row(conn, d, venue)
                if row is None:
                    raise CalendarNotBuilt(f"{d} outside built range")
                if not row.is_trading:
                    bars_on_closed_days.append(
                        {"security_id": sec_id, "date": d.isoformat(), "note": row.note}
                    )
            span_start, span_end = min(dates), max(dates)
            rows = (
                await conn.execute(
                    select(calendar_days.c.date).where(
                        calendar_days.c.venue == venue,
                        calendar_days.c.is_trading.is_(True),
                        calendar_days.c.date >= span_start,
                        calendar_days.c.date <= span_end,
                    )
                )
            ).scalars().all()
            for d in rows:
                if d not in dates:
                    missing_on_trading_days.append(
                        {"security_id": sec_id, "date": d.isoformat()}
                    )

    return {
        "securities_checked": len(by_security),
        "bars_on_closed_days": bars_on_closed_days,
        "missing_on_trading_days": missing_on_trading_days,
    }
