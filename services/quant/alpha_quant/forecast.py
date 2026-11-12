"""Forward contracts and scoring. Called only by the private server bridge."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from alpha_quant.core import DataError, calendar, decimal, sessions, wire

VERSION = "forward-forecast-v1-decimal34"


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DataError("timestamp_timezone_required")
    return parsed


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def signed(value):
    if not isinstance(value, str) or len(value) > 64:
        raise DataError("decimal_string_required")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise DataError("invalid_decimal") from exc
    if not number.is_finite() or abs(number) > Decimal("1e9"):
        raise DataError("invalid_decimal")
    return number


def contract(values):
    mean, lower, upper, probability = (
        signed(values[k]) for k in ("meanReturn", "lowerReturn", "upperReturn", "upProbability")
    )
    if lower < -1 or not lower <= mean <= upper or not 0 <= probability <= 1:
        raise DataError("invalid_forecast_distribution")
    return mean, lower, upper, probability


def forecast_plan(published_at, horizon):
    if horizon not in (5, 20):
        raise DataError("unsupported_forecast_horizon")
    now = timestamp(published_at)
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    cal = calendar(day.year, day.year + 1)
    upcoming = sessions(day.isoformat(), (day + timedelta(days=90)).isoformat())
    future = [d for d in upcoming if cal.session_open(d).to_pydatetime() > now]
    start, target = future[0], future[horizon - 1]
    close = cal.session_close(target).to_pydatetime()
    return {
        "startSession": start,
        "targetSession": target,
        "startOpenAt": iso(cal.session_open(start).to_pydatetime()),
        "targetCloseAt": iso(close),
        "evaluateAfter": iso(close + timedelta(minutes=20)),
        "horizon": horizon,
        "codeVersion": VERSION,
    }


def research_schedule(as_of):
    now = timestamp(as_of)
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    cal = calendar(day.year, day.year + 1)
    days = sessions((day - timedelta(days=60)).isoformat(), (day + timedelta(days=90)).isoformat())
    current = day.isoformat() if day.isoformat() in days else None
    completed = [
        d for d in days if cal.session_close(d).to_pydatetime() + timedelta(minutes=20) <= now
    ]
    future = [d for d in days if d > day.isoformat()]
    return {
        "session": current,
        "marketOpen": iso(cal.session_open(current).to_pydatetime()) if current else None,
        "marketClose": iso(cal.session_close(current).to_pydatetime()) if current else None,
        "latestCompletedSession": completed[-1],
        "recentCompletedSessions": completed[-21:],
        "temporaryExpirySession": future[19],
    }


def score(values, actual):
    mean, lower, upper, probability = contract(values)
    up = Decimal(1) if actual > 0 else Decimal(0)
    interval = (
        upper - lower + 10 * max(lower - actual, Decimal(0)) + 10 * max(actual - upper, Decimal(0))
    )
    return {
        "absoluteError": wire(abs(mean - actual)),
        "squaredError": wire((mean - actual) ** 2),
        "brier": wire((probability - up) ** 2),
        "intervalScore": wire(interval),
        "intervalCovered": lower <= actual <= upper,
        "directionCorrect": (mean > 0) == (actual > 0),
    }


def outcome(rows, start, target):
    mapped = {}
    for row in rows:
        if row.get("session") in mapped:
            raise DataError("duplicate_market_session")
        mapped[row.get("session")] = row
    first, last = mapped.get(start), mapped.get(target)
    if not first or not last or first.get("open") is None or last.get("close") is None:
        return None
    opening = decimal(first["open"], positive=True)
    closing = decimal(last["close"], positive=True)
    return {
        "startOpen": wire(opening),
        "targetClose": wire(closing),
        "actualReturn": wire(closing / opening - 1),
    }


def evaluate(data):
    with localcontext() as ctx:
        ctx.prec = 34
        stock = outcome(data["rows"], data["startSession"], data["targetSession"])
        benchmark = outcome(data["benchmarkRows"], data["startSession"], data["targetSession"])
        if stock is None or benchmark is None:
            return {
                "status": "pending",
                "reason": "missing_settlement_prices",
                "stockAvailable": stock is not None,
                "benchmarkAvailable": benchmark is not None,
            }
        actual = Decimal(stock["actualReturn"])
        result = {
            "status": "resolved",
            "codeVersion": VERSION,
            **stock,
            "benchmark": {"symbol": "SPY", **benchmark},
            "excessReturn": wire(actual - Decimal(benchmark["actualReturn"])),
            "zeroBaseline": {
                "absoluteError": wire(abs(actual)),
                "squaredError": wire(actual**2),
                "brier": "0.25",
            },
        }
        if "control" in data:
            result["control"] = score(data["control"], actual)
            result["candidate"] = score(data["candidate"], actual)
        else:
            result["metrics"] = score(data["forecast"], actual)
        return result


def assess_batch(samples):
    if not samples:
        raise DataError("empty_experience_batch")
    with localcontext() as ctx:
        ctx.prec = 34
        metrics = {}
        for metric in ("brier", "absoluteError", "intervalScore"):
            metrics[metric] = {
                arm: sum(signed(s[arm][metric]) for s in samples) / len(samples)
                for arm in ("control", "candidate")
            }
        improved = metrics["brier"]["control"] - metrics["brier"]["candidate"] >= Decimal(".01")
        acceptable = all(
            metrics[m]["candidate"] <= metrics[m]["control"] * Decimal("1.05")
            for m in ("absoluteError", "intervalScore")
        )
        return {
            "passes": improved and acceptable,
            "metrics": {k: {a: wire(v) for a, v in arms.items()} for k, arms in metrics.items()},
            "sampleCount": len(samples),
            "codeVersion": VERSION,
        }
