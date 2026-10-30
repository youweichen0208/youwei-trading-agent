"""Explicit synthetic REPLAY data, never a fallback for a real provider."""

from datetime import date, timedelta
from decimal import Decimal, localcontext

from yta_alpha.calendar import TradingCalendar
from yta_alpha.models import DataMode, MarketBar, MarketSnapshot

REPLAY_BASE_SESSION = date(2024, 11, 22)


def replay_snapshot(calendar: TradingCalendar, *, count: int = 80) -> MarketSnapshot:
    base = REPLAY_BASE_SESSION
    days = calendar.sessions(calendar.after(base, -(count - 1)), base)
    with localcontext() as context:
        context.prec = 50
        bars = []
        for i, session in enumerate(days):
            close = Decimal(100) / Decimal("1.001") ** (len(days) - 1 - i)
            bars.append(
                MarketBar(
                    session=session,
                    open=close,
                    high=close * Decimal("1.01"),
                    low=close * Decimal("0.99"),
                    close=close,
                    volume=1000000,
                    available_at=calendar.close_at(session) + timedelta(minutes=5),
                    retrieved_at=calendar.close_at(session) + timedelta(minutes=6),
                    revision="synthetic-v1",
                )
            )
    return MarketSnapshot(
        symbol="NVDA",
        provider="synthetic-fixture",
        source_id="yta:fixture:momentum20-v1",
        data_mode=DataMode.REPLAY,
        adjustment="RAW",
        as_of=calendar.close_at(base) + timedelta(minutes=10),
        bars=tuple(bars),
        actions_verified_through=base,
    )


def replay_observation(calendar: TradingCalendar, close: str = "102") -> MarketSnapshot:
    target = calendar.after(REPLAY_BASE_SESSION, 5)
    price = Decimal(close)
    at = calendar.close_at(target) + timedelta(minutes=10)
    return MarketSnapshot(
        symbol="NVDA",
        provider="synthetic-fixture",
        source_id="yta:fixture:outcome-v1",
        data_mode=DataMode.REPLAY,
        adjustment="RAW",
        as_of=at,
        actions_verified_through=target,
        bars=(
            MarketBar(
                session=target,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1000000,
                available_at=at,
                retrieved_at=at,
                revision=f"synthetic-close-{close}",
            ),
        ),
    )


def replay_base_revision(calendar: TradingCalendar, close: str = "101") -> MarketSnapshot:
    """Explicit synthetic correction; never used as a live-provider fallback."""
    original = replay_snapshot(calendar, count=21)
    at = replay_observation(calendar).as_of + timedelta(hours=1)
    price = Decimal(close)
    bar = original.bars[-1].model_copy(
        update={
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "revision": f"synthetic-base-{close}",
            "available_at": at,
            "retrieved_at": at,
        }
    )
    return original.model_copy(
        update={"bars": (bar,), "as_of": at, "source_id": "yta:fixture:base-revision-v1"}
    )
