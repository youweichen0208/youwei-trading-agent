"""Internal calculation functions; never expose raw-price arguments as Agent tools."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache

import exchange_calendars as xcals

ZERO = Decimal(0)
VERSION = "quant-v1-decimal34"


class DataError(ValueError):
    pass


def decimal(value: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 64:
        raise DataError("decimal_string_required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DataError("invalid_decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise DataError("invalid_decimal")
    if result != 0 and not -18 <= result.adjusted() <= 18:
        raise DataError("decimal_out_of_range")
    return result


@dataclass(frozen=True)
class Close:
    session: str
    close: str | None


@lru_cache(maxsize=16)
def calendar(start_year: int, end_year: int):
    return xcals.get_calendar(
        "XNYS", start=f"{start_year - 1}-01-01", end=f"{end_year + 1}-12-31"
    )


def sessions(start: str, end: str) -> list[str]:
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
        if start > end:
            raise ValueError("reversed")
        cal = calendar(date.fromisoformat(start).year, date.fromisoformat(end).year)
        return [d.strftime("%Y-%m-%d") for d in cal.sessions_in_range(start, end)]
    except (ValueError, TypeError) as exc:
        raise DataError("invalid_session_range") from exc


def validate(rows: list[Close]) -> list[Decimal | None]:
    if not rows:
        raise DataError("no_data")
    dates = [r.session for r in rows]
    if dates != sessions(dates[0], dates[-1]):
        raise DataError("missing_duplicate_or_unordered_sessions")
    return [decimal(r.close, positive=True) if r.close is not None else None for r in rows]


def indicators(rows: list[Close]) -> list[dict]:
    prices = validate(rows)
    output = []
    contiguous: list[Decimal] = []
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    avg_gain = avg_loss = None
    with localcontext() as ctx:
        ctx.prec = 34
        for row, price in zip(rows, prices, strict=True):
            ma = rsi = None
            if price is None:
                contiguous, gains, losses = [], [], []
                avg_gain = avg_loss = None
            else:
                if contiguous:
                    delta = price - contiguous[-1]
                    gain, loss = max(delta, ZERO), max(-delta, ZERO)
                    gains.append(gain)
                    losses.append(loss)
                    if avg_gain is None and len(gains) == 14:
                        avg_gain, avg_loss = sum(gains) / 14, sum(losses) / 14
                    elif avg_gain is not None:
                        avg_gain = (avg_gain * 13 + gain) / 14
                        avg_loss = (avg_loss * 13 + loss) / 14
                    if avg_gain is not None:
                        rsi = (
                            Decimal(50)
                            if avg_gain == avg_loss == 0
                            else Decimal(100)
                            if avg_loss == 0
                            else 100 - 100 / (1 + avg_gain / avg_loss)
                        )
                contiguous.append(price)
                if len(contiguous) >= 20:
                    ma = sum(contiguous[-20:]) / 20
            output.append({"session": row.session, "ma20": wire(ma), "rsi14": wire(rsi)})
    return output


def wire(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def prediction(rows: list[Close], *, adjustment: str) -> dict:
    if adjustment != "split":
        raise DataError("prediction_requires_split_adjusted_prices")
    prices = validate(rows)
    base = {"codeVersion": VERSION, "label": "探索性统计基线", "evaluation": "retrospective"}
    if len(prices) < 81 or any(p is None for p in prices[-81:]):
        return {**base, "status": "unavailable", "reason": "insufficient_contiguous_samples"}
    prices = prices[-81:]
    dates = [r.session for r in rows[-81:]]
    with localcontext() as ctx:
        ctx.prec = 34
        returns = [prices[i] / prices[i - 1] - 1 for i in range(1, 81)]
        points = []
        errors, baseline_errors = [], []
        for i in range(20, 80):
            forecast = sum(returns[i - 20 : i]) / 20
            actual = returns[i]
            errors.append(forecast - actual)
            baseline_errors.append(actual)
            points.append(
                {
                    "origin": dates[i],
                    "target": dates[i + 1],
                    "prediction": wire(forecast),
                    "actual": wire(actual),
                }
            )

        def metrics(values):
            return {
                "mae": wire(sum(abs(v) for v in values) / len(values)),
                "rmse": wire((sum(v * v for v in values) / len(values)).sqrt()),
            }

        cal = calendar(date.fromisoformat(dates[-1]).year, date.fromisoformat(dates[-1]).year)
        target = cal.next_session(dates[-1]).strftime("%Y-%m-%d")
        return {
            **base,
            "status": "available",
            "origin": dates[-1],
            "target": target,
            "prediction": wire(sum(returns[-20:]) / 20),
            "points": points,
            "metrics": metrics(errors),
            "zeroBaseline": metrics(baseline_errors),
        }


def portfolio(positions: list[dict], cash: str, quotes: dict, *, as_of: str) -> dict:
    # Caller supplies an immutable user-owned holdings snapshot and resolved quote dataset.
    date.fromisoformat(as_of)
    if as_of not in sessions(as_of, as_of):
        raise DataError("not_a_session")
    cash_value = decimal(cash)
    seen = set()
    lines, missing = [], []
    with localcontext() as ctx:
        ctx.prec = 34
        for position in positions:
            symbol = position["symbol"]
            if not isinstance(symbol, str) or not symbol or symbol in seen:
                raise DataError("invalid_or_duplicate_symbol")
            seen.add(symbol)
            quantity = decimal(position["quantity"])
            quote = quotes.get(symbol)
            value, reason = None, None
            if quantity == 0:
                value = ZERO
            elif not quote:
                reason = "missing_price"
            elif quote.get("session") != as_of or quote.get("adjustment") != "raw":
                reason = "inconsistent_price_basis"
            else:
                value = quantity * decimal(quote["close"], positive=True)
            if reason:
                missing.append({"symbol": symbol, "reason": reason})
            lines.append({"symbol": symbol, "value": wire(value), "weight": None})
        total = None if missing else cash_value + sum(Decimal(x["value"]) for x in lines)
        weights = []
        cash_weight = None
        if total is not None and total > 0:
            cash_weight = cash_value / total
            weights.append(cash_weight)
            for line in lines:
                weight = Decimal(line["value"]) / total
                weights.append(weight)
                line["weight"] = wire(weight)
        return {
            "label": "模拟持仓",
            "asOf": as_of,
            "positions": lines,
            "missing": missing,
            "total": wire(total),
            "cashWeight": wire(cash_weight),
            "maxWeight": wire(max(weights)) if weights else None,
            "concentration": wire(sum(w * w for w in weights)) if weights else None,
        }
