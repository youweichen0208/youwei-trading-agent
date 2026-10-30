from datetime import date
from decimal import Decimal

from yta_alpha.fixtures import replay_snapshot
from yta_alpha.indicators import ema, indicators, rsi, sma


def test_holidays_early_close_and_dst(calendar):
    assert calendar.after(date(2024, 11, 27), 1) == date(2024, 11, 29)
    assert calendar.close_at(date(2024, 11, 29)).hour == 18  # 13:00 EST
    assert calendar.open_at(date(2024, 3, 8)).hour == 14
    assert calendar.open_at(date(2024, 3, 11)).hour == 13
    assert calendar.after(date(2024, 7, 3), 1) == date(2024, 7, 5)


def test_known_indicator_reference_sequences():
    values = tuple(Decimal(i) for i in range(1, 22))
    assert sma(values, 20) == [None] * 19 + [Decimal("10.5"), Decimal("11.5")]
    assert ema(values, 3)[:5] == [None, None, Decimal(2), Decimal(3), Decimal(4)]
    assert rsi(values)[14:] == [Decimal(100)] * 7
    assert rsi(tuple(reversed(values)))[14:] == [Decimal(0)] * 7
    assert rsi((Decimal(10),) * 21)[14:] == [Decimal(50)] * 7


def test_constant_price_reference_and_warmup(calendar):
    snapshot = replay_snapshot(calendar, count=60)
    bars = tuple(
        b.model_copy(
            update={
                "open": Decimal(10),
                "close": Decimal(10),
                "high": Decimal(11),
                "low": Decimal(9),
            }
        )
        for b in snapshot.bars
    )
    result = indicators(snapshot.model_copy(update={"bars": bars}), calendar)
    series = result["series"]
    assert series["SMA20"][:19] == [None] * 19
    assert series["MACD_SIGNAL"][:33] == [None] * 33
    assert Decimal(series["MACD"][-1]) == 0
    assert Decimal(series["MACD_SIGNAL"][-1]) == 0
    assert Decimal(series["BOLL_UPPER"][-1]) == 10
    assert Decimal(series["BOLL_LOWER"][-1]) == 10
    assert Decimal(series["ATR14"][-1]) == 2


def test_returns_volume_volatility_independent_reference(calendar):
    import math
    import random
    import statistics
    from decimal import localcontext

    import pytest

    rng = random.Random(20261027)
    snapshot = replay_snapshot(calendar, count=60)
    prices = [Decimal(100 + rng.randrange(-20, 21)) for _ in snapshot.bars]
    volumes = [rng.randrange(100, 10000) for _ in snapshot.bars]
    volumes[3] = 0
    bars = tuple(
        bar.model_copy(
            update=dict(open=price, close=price, high=price + 1, low=price - 1, volume=volume)
        )
        for bar, price, volume in zip(snapshot.bars, prices, volumes, strict=True)
    )
    source = snapshot.model_copy(update={"bars": bars})
    with localcontext() as context:
        context.prec = 7  # the API must own calculation precision
        result = indicators(source, calendar)
    assert result["schema_version"] == "indicators-v3"
    series = result["series"]
    reference = [float(b) / float(a) - 1 for a, b in zip(prices, prices[1:], strict=False)]
    assert series["RETURN_1D"][0] is None
    assert series["VOLUME_CHANGE_1D"][0] is None
    assert series["VOLUME_CHANGE_1D"][4] is None
    assert Decimal(series["VOLUME_CHANGE_1D"][3]) == -1
    assert series["HIST_VOL20"][:20] == [None] * 20
    for i in range(1, 60):
        assert float(series["RETURN_1D"][i]) == pytest.approx(reference[i - 1], abs=1e-12)
        if volumes[i - 1]:
            assert float(series["VOLUME_CHANGE_1D"][i]) == pytest.approx(
                volumes[i] / volumes[i - 1] - 1, abs=1e-12
            )
    for i in range(20, 60):
        expected = statistics.stdev(reference[i - 20 : i]) * math.sqrt(252)
        assert float(series["HIST_VOL20"][i]) == pytest.approx(expected, abs=1e-12)
    short = indicators(source.model_copy(update={"bars": bars[-20:]}), calendar)
    assert short["series"]["HIST_VOL20"] == [None] * 20


def test_split_neutrality_missing_sessions_and_future_evidence(calendar):
    from datetime import timedelta

    import pytest
    from yta_alpha.errors import DomainError
    from yta_alpha.models import Split

    snapshot = replay_snapshot(calendar, count=30)
    bars = tuple(
        bar.model_copy(
            update=dict(
                open=Decimal(100 if i < 15 else 50),
                close=Decimal(100 if i < 15 else 50),
                high=Decimal(100 if i < 15 else 50),
                low=Decimal(100 if i < 15 else 50),
                volume=1000 if i < 15 else 2000,
            )
        )
        for i, bar in enumerate(snapshot.bars)
    )
    split = Split(
        effective_session=bars[15].session,
        new_shares_per_old=Decimal(2),
        available_at=bars[15].available_at,
        source_id="synthetic-split",
    )
    source = snapshot.model_copy(update={"bars": bars, "splits": (split,)})
    result = indicators(source, calendar)
    assert result["share_basis_session"] == bars[-1].session.isoformat()
    assert len(result["data_snapshot_hash"]) == 64
    assert all(Decimal(bar["close"]) == 50 for bar in result["bars"])
    assert all(Decimal(bar["volume"]) == 2000 for bar in result["bars"])
    assert Decimal(result["bars"][0]["split_factor"]) == 2
    assert Decimal(result["bars"][-1]["split_factor"]) == 1
    assert source.bars[0].close == 100 and source.bars[0].volume == 1000
    series = result["series"]
    for name, warmup in (("RETURN_1D", 1), ("VOLUME_CHANGE_1D", 1), ("HIST_VOL20", 20)):
        assert all(Decimal(value) == 0 for value in series[name][warmup:])
    with pytest.raises(DomainError, match="missing"):
        indicators(source.model_copy(update={"bars": bars[:10] + bars[11:]}), calendar)
    future = split.model_copy(update={"available_at": snapshot.as_of + timedelta(seconds=1)})
    with pytest.raises(DomainError) as error:
        indicators(source.model_copy(update={"splits": (future,)}), calendar)
    assert error.value.code == "FUTURE_INFORMATION"


def test_existing_indicators_against_independent_pandas_reference(calendar):
    import random

    import pandas as pd
    import pytest

    rng = random.Random(53)
    snapshot = replay_snapshot(calendar, count=90)
    prices = [Decimal(100 + rng.randrange(-30, 31)) for _ in snapshot.bars]
    bars = tuple(
        bar.model_copy(update=dict(open=price, close=price, high=price + 3, low=price - 2))
        for bar, price in zip(snapshot.bars, prices, strict=True)
    )
    output = indicators(snapshot.model_copy(update={"bars": bars}), calendar)["series"]
    close = pd.Series([float(p) for p in prices])

    def seeded_ewm(series, window, alpha):
        valid = series.dropna()
        seed_index = valid.index[window - 1]
        seed = pd.Series([valid.iloc[:window].mean()], index=[seed_index])
        return (
            pd.concat([seed, valid.iloc[window:]])
            .ewm(alpha=alpha, adjust=False)
            .mean()
            .reindex(close.index)
        )

    e12 = seeded_ewm(close, 12, 2 / 13)
    e26 = seeded_ewm(close, 26, 2 / 27)
    macd = e12 - e26
    signal = seeded_ewm(macd, 9, 2 / 10)
    changes = close.diff()
    gain = seeded_ewm(changes.clip(lower=0), 14, 1 / 14)
    loss = seeded_ewm((-changes).clip(lower=0), 14, 1 / 14)
    middle = close.rolling(20).mean()
    deviation = close.rolling(20).std(ddof=0)
    true_range = pd.concat(
        [
            pd.Series(5.0, index=close.index),
            (close + 3 - close.shift()).abs(),
            (close - 2 - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    references = {
        "SMA20": middle,
        "SMA50": close.rolling(50).mean(),
        "EMA12": e12,
        "EMA26": e26,
        "RSI14": 100 - 100 / (1 + gain / loss),
        "MACD": macd,
        "MACD_SIGNAL": signal,
        "MACD_HISTOGRAM": macd - signal,
        "BOLL_MIDDLE": middle,
        "BOLL_UPPER": middle + 2 * deviation,
        "BOLL_LOWER": middle - 2 * deviation,
        "ATR14": seeded_ewm(true_range, 14, 1 / 14),
    }
    for name, expected in references.items():
        for actual, reference in zip(output[name], expected, strict=True):
            if pd.isna(reference):
                assert actual is None, name
            else:
                assert float(actual) == pytest.approx(reference, abs=1e-10, rel=0), name


def test_chart_ohlcv_uses_exact_reverse_split_factor(calendar):
    from yta_alpha.forecast import snapshot_hash
    from yta_alpha.models import Split

    snapshot = replay_snapshot(calendar, count=30)
    split_day = snapshot.bars[15].session
    split = Split(
        effective_session=split_day,
        new_shares_per_old=Decimal("0.5"),
        available_at=snapshot.bars[15].available_at,
        source_id="reverse-fixture",
    )
    bars = tuple(
        bar.model_copy(
            update=dict(
                open=Decimal(10), high=Decimal(12), low=Decimal(9), close=Decimal(11), volume=3
            )
        )
        for bar in snapshot.bars
    )
    source = snapshot.model_copy(update={"bars": bars, "splits": (split,)})
    result = indicators(source, calendar)
    first = result["bars"][0]
    assert {key: Decimal(first[key]) for key in ("open", "high", "low", "close", "volume")} == {
        "open": Decimal(20),
        "high": Decimal(24),
        "low": Decimal(18),
        "close": Decimal(22),
        "volume": Decimal("1.5"),
    }
    assert Decimal(result["bars"][-1]["close"]) == 11
    assert Decimal(result["bars"][-1]["volume"]) == 3
    assert result["data_snapshot_hash"] == snapshot_hash(source)
