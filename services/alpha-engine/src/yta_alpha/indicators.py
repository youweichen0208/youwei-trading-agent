from decimal import Decimal, localcontext

from yta_alpha.calendar import TradingCalendar
from yta_alpha.forecast import adjusted_closes, rounded, snapshot_hash, validate_snapshot
from yta_alpha.models import MarketSnapshot


def sma(values: tuple[Decimal, ...], window: int) -> list[Decimal | None]:
    return [
        None if i < window - 1 else sum(values[i - window + 1 : i + 1]) / window
        for i in range(len(values))
    ]


def ema(values: tuple[Decimal, ...], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < window:
        return result
    previous = sum(values[:window]) / window
    result[window - 1] = previous
    weight = Decimal(2) / (window + 1)
    for i in range(window, len(values)):
        previous = values[i] * weight + previous * (1 - weight)
        result[i] = previous
    return result


def rsi(values: tuple[Decimal, ...], window: int = 14) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) <= window:
        return result
    changes = [right - left for left, right in zip(values, values[1:], strict=False)]
    gain = sum((max(d, Decimal(0)) for d in changes[:window]), Decimal(0)) / window
    loss = sum((max(-d, Decimal(0)) for d in changes[:window]), Decimal(0)) / window
    for i in range(window, len(values)):
        if i > window:
            gain = (gain * (window - 1) + max(changes[i - 1], 0)) / window
            loss = (loss * (window - 1) + max(-changes[i - 1], 0)) / window
        result[i] = (
            Decimal(50)
            if gain == loss == 0
            else (Decimal(100) if loss == 0 else 100 - 100 / (1 + gain / loss))
        )
    return result


def indicators(snapshot: MarketSnapshot, calendar: TradingCalendar) -> dict:
    validate_snapshot(snapshot, calendar)
    sessions = tuple(b.session for b in snapshot.bars)
    if sessions != calendar.sessions(sessions[0], sessions[-1]):
        from yta_alpha.errors import DomainError

        raise DomainError("MISSING_SESSION", "Indicators cannot silently bridge missing sessions")
    with localcontext() as context:
        context.prec = 50
        values = adjusted_closes(snapshot)
        e12, e26 = ema(values, 12), ema(values, 26)
        macd = [
            a - b if a is not None and b is not None else None
            for a, b in zip(e12, e26, strict=True)
        ]
        valid_macd = tuple(v for v in macd if v is not None)
        signal = [None] * (len(values) - len(valid_macd)) + ema(valid_macd, 9)
        middle = sma(values, 20)
        upper: list[Decimal | None] = [None] * len(values)
        lower: list[Decimal | None] = [None] * len(values)
        for i, mean in enumerate(middle):
            if mean is not None:
                variance = sum((x - mean) ** 2 for x in values[i - 19 : i + 1]) / 20
                upper[i], lower[i] = mean + 2 * variance.sqrt(), mean - 2 * variance.sqrt()
        true_ranges: list[Decimal] = []
        for i, bar in enumerate(snapshot.bars):
            factor = values[i] / bar.close
            high, low = bar.high * factor, bar.low * factor
            previous = values[i - 1] if i else values[i]
            true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        atr: list[Decimal | None] = [None] * len(values)
        if len(values) >= 14:
            previous_atr = sum(true_ranges[:14]) / 14
            atr[13] = previous_atr
            for i in range(14, len(values)):
                previous_atr = (previous_atr * 13 + true_ranges[i]) / 14
                atr[i] = previous_atr
        returns: list[Decimal | None] = [None] + [
            right / left - 1 for left, right in zip(values, values[1:], strict=False)
        ]
        # One response carries chart bars and indicators from the exact same snapshot.
        chart_bars = []
        volumes: list[Decimal] = []
        for bar in snapshot.bars:
            factor = Decimal(1)
            for split in snapshot.splits:
                if bar.session < split.effective_session <= sessions[-1]:
                    factor *= split.new_shares_per_old
            volume = Decimal(bar.volume) * factor
            volumes.append(volume)
            chart_bars.append(
                {
                    "session": bar.session.isoformat(),
                    **{
                        name: format(getattr(bar, name) / factor, "f")
                        for name in ("open", "high", "low", "close")
                    },
                    "volume": format(volume, "f"),
                    "split_factor": format(factor, "f"),
                }
            )
        volume_changes: list[Decimal | None] = [None] + [
            right / left - 1 if left else None
            for left, right in zip(volumes, volumes[1:], strict=False)
        ]
        volatility: list[Decimal | None] = [None] * len(values)
        for i in range(20, len(values)):
            window = returns[i - 19 : i + 1]
            observed = [value for value in window if value is not None]
            mean = sum(observed) / 20
            variance = sum((value - mean) ** 2 for value in observed) / 19
            volatility[i] = (variance * 252).sqrt()
        series = {
            "RETURN_1D": returns,
            "VOLUME_CHANGE_1D": volume_changes,
            "HIST_VOL20": volatility,
            "SMA20": sma(values, 20),
            "SMA50": sma(values, 50),
            "EMA12": e12,
            "EMA26": e26,
            "RSI14": rsi(values),
            "MACD": macd,
            "MACD_SIGNAL": signal,
            "MACD_HISTOGRAM": [
                a - b if a is not None and b is not None else None
                for a, b in zip(macd, signal, strict=True)
            ],
            "BOLL_MIDDLE": middle,
            "BOLL_UPPER": upper,
            "BOLL_LOWER": lower,
            "ATR14": atr,
        }
        return {
            "symbol": snapshot.symbol,
            "provider": snapshot.provider,
            "as_of": snapshot.as_of.isoformat(),
            "data_mode": snapshot.data_mode.value,
            "adjustment": "SPLIT_ONLY_AS_OF",
            "schema_version": "indicators-v3",
            "share_basis_session": sessions[-1].isoformat(),
            "data_snapshot_hash": snapshot_hash(snapshot),
            "bars": chart_bars,
            "definitions": {
                "RETURN_1D": "split-comparable close / previous close - 1; first null",
                "VOLUME_CHANGE_1D": (
                    "split-comparable shares / previous shares - 1; first or zero denominator null"
                ),
                "HIST_VOL20": "sample stdev of 20 simple daily returns * sqrt(252); first 20 null",
            },
            "warmup_policy": "null; EMA seeded with SMA; RSI/ATR Wilder; BOLL population variance",
            "sessions": [s.isoformat() for s in sessions],
            "series": {
                key: [format(rounded(v), "f") if v is not None else None for v in items]
                for key, items in series.items()
            },
        }
