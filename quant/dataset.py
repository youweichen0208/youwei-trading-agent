"""Pure TargetSpec dataset construction from an explicit frozen calendar and bars.

Historical rows describe a retrospective study of today's frozen panel. Their
grace deadline is an *assumed* historical label availability time; it does not
prove when a vendor originally published a value or whether a value was revised.
For forward use, callers must enforce PIT/version permissions before passing
frozen bars to ``compute_features``. No provider calls or database reads occur.
"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from io import BytesIO
from math import sqrt
from statistics import pstdev
from zoneinfo import ZoneInfo

from tzdata import IANA_VERSION


FEATURE_NAMES = (
    "excess_momentum_20d",
    "excess_momentum_60d",
    "volatility_20d",
)
NUMERIC_SCALE = Decimal("0.0000000001")
NY = ZoneInfo.from_file(
    BytesIO(files("tzdata.zoneinfo").joinpath("America/New_York").read_bytes()),
    key="America/New_York",
)


def _instant(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calendar and cutoff timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _day(value: str | date) -> str:
    return date.fromisoformat(value).isoformat() if isinstance(value, str) else value.isoformat()


def _sessions(values: list[dict]) -> list[dict]:
    result = [
        {
            "date": _day(value["date"]),
            "open_utc": _instant(value["open_utc"]),
            "close_utc": _instant(value["close_utc"]),
        }
        for value in values
    ]
    result.sort(key=lambda value: value["date"])
    if len({value["date"] for value in result}) != len(result):
        raise ValueError("duplicate calendar session")
    for value in result:
        if value["open_utc"] >= value["close_utc"]:
            raise ValueError("calendar open must precede close")
        for field in ("open_utc", "close_utc"):
            if value[field].astimezone(NY).date().isoformat() != value["date"]:
                raise ValueError("calendar date must match its New York session")
    return result


def _bar_index(values: list[dict]) -> dict[str, dict]:
    result = {}
    for value in values:
        day = _day(value["trade_date"])
        if day in result:
            raise ValueError(f"duplicate frozen bar on {day}")
        result[day] = value
    return result


def _dec(value) -> Decimal:
    return Decimal(str(value))


def total_return_from_bars(bars: list[dict]) -> Decimal:
    """First OPEN to last CLOSE, split first and ex-date-close reinvestment.

    The first session's dividend and split are excluded: the position is entered
    after those actions. This matches the Ledger's ``total-return-v1`` convention.
    Callers validate completeness, chronological order and positive prices.
    """
    if not bars:
        raise ValueError("no bars to compute a return from")
    units = Decimal(1) / _dec(bars[0]["open"])
    for bar in bars[1:]:
        units *= _dec(bar["split_factor"])
        dividend = _dec(bar["div_cash"])
        if dividend > 0:
            units *= 1 + dividend / _dec(bar["close"])
    return (units * _dec(bars[-1]["close"]) - 1).quantize(NUMERIC_SCALE)


def _close_return(bars: list[dict]) -> Decimal:
    # Only the initial price differs for a close-to-close feature; subsequent
    # sessions preserve the exact same corporate-action entitlement rules.
    first = dict(bars[0], open=bars[0]["close"])
    return total_return_from_bars([first, *bars[1:]])


def _missing_bars(index: dict, security_ids: tuple[str, ...], dates: list[str], stage: str) -> list[dict]:
    missing = []
    for security_id in security_ids:
        for day in dates:
            bar = index.get(security_id, {}).get(day)
            reason = None
            if bar is None:
                reason = "missing_bar"
            elif not _bar_valid(bar):
                reason = "invalid_bar"
            if reason:
                missing.append({"stage": stage, "security_id": security_id, "trade_date": day, "reason": reason})
    return missing


def _bar_valid(bar: dict) -> bool:
    if bar.get("quality") != "ok":
        return False
    try:
        positive = [_dec(bar[field]) for field in ("open", "close", "split_factor")]
        if "volume" in bar:
            positive.append(_dec(bar["volume"]))
        dividend = _dec(bar["div_cash"])
        return (
            all(value.is_finite() and value > 0 for value in positive)
            and dividend.is_finite()
            and dividend >= 0
        )
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return False


def _features(index: dict, sessions: list[dict], cutoff: datetime, security_id: str, benchmark_id: str) -> dict:
    history = [session for session in sessions if session["close_utc"] <= cutoff]
    dates = [session["date"] for session in history[-61:]]
    if len(dates) < 61:
        return {"status": "unavailable", "features": None, "missing": [{"stage": "features", "reason": "insufficient_calendar_history"}]}
    missing = _missing_bars(index, (security_id, benchmark_id), dates, "features")
    if missing:
        return {"status": "unavailable", "features": None, "missing": missing}
    stock = [index[security_id][day] for day in dates]
    benchmark = [index[benchmark_id][day] for day in dates]
    momentum_20 = _close_return(stock[-21:]) - _close_return(benchmark[-21:])
    momentum_60 = _close_return(stock) - _close_return(benchmark)
    daily = [float(_close_return(stock[i - 1:i + 1])) for i in range(len(stock) - 20, len(stock))]
    return {
        "status": "available",
        "features": {
            "excess_momentum_20d": float(momentum_20),
            "excess_momentum_60d": float(momentum_60),
            "volatility_20d": pstdev(daily) * sqrt(252),
        },
        "missing": [],
    }


def compute_features(
    *,
    bars_by_security: dict[str, list[dict]],
    calendar_sessions: list[dict],
    cutoff: str | datetime,
    security_id: str,
    benchmark_security_id: str,
) -> dict:
    """Compute the three candidate features without skipping calendar holes.

    ``bars_by_security`` must be frozen/pre-filtered for the caller's PIT mode.
    Future session bars never enter a feature, even when present for labels.
    """
    sessions = _sessions(calendar_sessions)
    index = {key: _bar_index(value) for key, value in bars_by_security.items()}
    return _features(index, sessions, _instant(cutoff), security_id, benchmark_security_id)


def build_dataset(
    *,
    bars_by_security: dict[str, list[dict]],
    calendar_sessions: list[dict],
    cutoffs: list[str | datetime],
    panel_security_ids: list[str],
    benchmark_security_id: str,
    horizons_td: tuple[int, ...] | list[int] = (1, 20, 60),
) -> dict:
    """Preserve every security × cutoff × horizon planned row, including gaps."""
    if not horizons_td or any(type(h) is not int or h not in (1, 20, 60) for h in horizons_td):
        raise ValueError("TargetSpec horizons must be drawn from 1, 20, 60")
    if len(set(horizons_td)) != len(horizons_td):
        raise ValueError("duplicate planned horizon")
    if not panel_security_ids or len(set(panel_security_ids)) != len(panel_security_ids):
        raise ValueError("panel security IDs must be nonempty and unique")
    normalized_cutoffs = [_instant(cutoff) for cutoff in cutoffs]
    if len(set(normalized_cutoffs)) != len(normalized_cutoffs):
        raise ValueError("duplicate planned cutoff")
    sessions = _sessions(calendar_sessions)
    index = {key: _bar_index(value) for key, value in bars_by_security.items()}
    rows = []
    for cutoff in normalized_cutoffs:
        local = cutoff.astimezone(NY)
        if (local.weekday(), local.hour, local.minute, local.second, local.microsecond) != (5, 6, 0, 0, 0):
            raise ValueError("weekly cutoff must be Saturday 06:00 America/New_York")
        entry_index = next((i for i, s in enumerate(sessions) if s["open_utc"] > cutoff), None)
        for security_id in panel_security_ids:
            features = _features(index, sessions, cutoff, security_id, benchmark_security_id)
            for horizon in horizons_td:
                row = {
                    "security_id": security_id,
                    "benchmark_security_id": benchmark_security_id,
                    "cutoff": cutoff.isoformat(),
                    "horizon_td": horizon,
                    "features": features["features"],
                    "feature_status": features["status"],
                    "entry_at": None,
                    "exit_at": None,
                    "assumed_label_available_at": None,
                    "label_status": "unresolved",
                    "label": None,
                    "excess_return": None,
                    "missing": list(features["missing"]),
                }
                rows.append(row)
                if entry_index is None:
                    row["missing"].append({"stage": "label", "reason": "missing_calendar_entry"})
                    continue
                row["entry_at"] = sessions[entry_index]["open_utc"].isoformat()
                exit_index = entry_index + horizon - 1
                if exit_index >= len(sessions):
                    row["missing"].append({"stage": "label", "reason": "missing_calendar_exit"})
                    continue
                row["exit_at"] = sessions[exit_index]["close_utc"].isoformat()
                if exit_index + 5 >= len(sessions):
                    row["missing"].append({"stage": "label", "reason": "missing_calendar_grace_deadline"})
                    continue
                row["assumed_label_available_at"] = sessions[exit_index + 5]["close_utc"].isoformat()
                dates = [s["date"] for s in sessions[entry_index:exit_index + 1]]
                missing = _missing_bars(index, (security_id, benchmark_security_id), dates, "label")
                row["missing"].extend(missing)
                if missing:
                    continue
                stock = [index[security_id][day] for day in dates]
                benchmark = [index[benchmark_security_id][day] for day in dates]
                excess = total_return_from_bars(stock) - total_return_from_bars(benchmark)
                row.update(label_status="resolved", label=int(excess > 0), excess_return=float(excess))
    return {
        "rows": rows,
        "metadata": {
            "data_mode": "historical_source",
            "evaluation_scope": "retrospective_current_panel",
            "label_availability_assumption": "original_exit_plus_five_calendar_sessions_close",
            "availability_is_observed": False,
            "features": list(FEATURE_NAMES),
            "tzdb_version": IANA_VERSION,
            "planned_rows": len(rows),
            "warning": "Current vendor revisions and current-panel survivorship remain; this is not evidence of historical PIT availability or forward ability.",
        },
    }
