"""Explicit next-open, long-or-cash research simulation; never an executable order."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, field_validator

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, price_return, rounded, snapshot_hash, validate_snapshot
from yta_alpha.models import Contract, DataMode, MarketSnapshot

Rate = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]


class ExecutionCosts(Contract):
    """Per-side fractions of executed notional, explicitly supplied without defaults."""

    commission_rate: Rate
    slippage_rate: Rate

    @field_validator("commission_rate", "slippage_rate", mode="before")
    @classmethod
    def exact_rates(cls, value):
        if isinstance(value, (bool, float)):
            raise ValueError("Cost rates must be exact decimal strings")
        return value


def simulate_trade(
    base: MarketSnapshot,
    observation: MarketSnapshot,
    *,
    action: Literal["LONG", "CASH"],
    holding_sessions: int,
    costs: ExecutionCosts,
    decision_at: datetime,
    cutoff: datetime,
    calendar: TradingCalendar,
) -> dict:
    """Price-only, all-in fractional-share episode with a matched long benchmark.

    Decide after the base close and strictly before the next regular-session open.
    Buy at that next open and exit after holding_sessions exchange sessions at open.
    Historical opens are simulation proxies, not evidence that an order could fill.
    Every holding session must have a positive-volume bar; gaps/halts are rejected,
    not skipped or rolled forward. Portfolio sizing, dividends, borrow, taxes and
    liquidity/auction eligibility need separate protocols and are not inferred here.
    """
    base = MarketSnapshot.model_validate(base.model_dump(mode="python"))
    observation = MarketSnapshot.model_validate(observation.model_dump(mode="python"))
    costs = ExecutionCosts.model_validate(costs.model_dump(mode="python"))
    if (
        action not in ("LONG", "CASH")
        or type(holding_sessions) is not int
        or holding_sessions not in (1, 3, 5, 10, 20)
    ):
        raise DomainError(
            "INVALID_BACKTEST_REQUEST", "Explicit action and supported holding period required"
        )
    if any(at.tzinfo is None or at.utcoffset() is None for at in (decision_at, cutoff)):
        raise DomainError("INVALID_TIME", "Backtest timestamps must include timezone")
    if (
        base.data_mode not in (DataMode.REPLAY, DataMode.DEMO)
        or observation.data_mode != base.data_mode
        or observation.symbol != base.symbol
    ):
        raise DomainError(
            "BACKTEST_COHORT_MISMATCH", "Backtests require isolated matching historical evidence"
        )
    validate_snapshot(base, calendar)
    validate_snapshot(observation, calendar)
    entry = calendar.after(base.bars[-1].session, 1)
    exit_session = calendar.after(entry, holding_sessions)
    if not base.as_of <= decision_at < calendar.open_at(entry):
        raise DomainError(
            "BACKTEST_DECISION_WINDOW", "Decision must precede entry and follow input availability"
        )
    if observation.as_of > cutoff or decision_at > cutoff:
        raise DomainError("FUTURE_INFORMATION", "Evidence exceeds backtest cutoff")
    if cutoff < calendar.close_at(exit_session):
        raise DomainError("NOT_DUE", "Final exit-session bar not yet available", status=409)
    bars = {bar.session: bar for bar in observation.bars}
    for session in calendar.sessions(entry, exit_session):
        if session not in bars:
            raise DomainError(
                "BACKTEST_MISSING_SESSION", "Exact execution/holding session evidence missing"
            )
        if bars[session].volume == 0:
            raise DomainError(
                "BACKTEST_EXECUTION_UNAVAILABLE", "Zero volume cannot prove an executable open"
            )
    for split in observation.splits:
        calendar.open_at(split.effective_session)
    first, last = bars[entry], bars[exit_session]
    gross, factor = price_return(first.open, last.open, entry, exit_session, observation.splits)
    with localcontext() as context:
        context.prec = 50
        one = Decimal(1)
        buy = first.open * (one + costs.slippage_rate)
        sell = last.open * factor * (one - costs.slippage_rate)
        # Capital includes entry commission. Exit commission is charged on sale proceeds.
        net = rounded(
            sell * (one - costs.commission_rate) / (buy * (one + costs.commission_rate)) - one
        )
        result = {
            "schema_version": "next-open-backtest-episode-v1",
            "metrics_kind": "BACKTEST",
            "qualification": "UNVERIFIED",
            "execution_assumption": "historical-open-proxy-no-fill-guarantee",
            "return_basis": "split-only-price-return-excluding-dividends",
            "symbol": base.symbol,
            "data_mode": base.data_mode.value,
            "calendar_version": calendar.version,
            "action": action,
            "holding_sessions": holding_sessions,
            "decision_at": decision_at.isoformat(),
            "cutoff": cutoff.isoformat(),
            "entry_session": entry.isoformat(),
            "exit_session": exit_session.isoformat(),
            "entry_at": calendar.open_at(entry).isoformat(),
            "exit_at": calendar.open_at(exit_session).isoformat(),
            "entry_open": str(first.open),
            "exit_open": str(last.open),
            "split_factor": str(factor),
            "costs": costs.model_dump(mode="json"),
            "benchmark_gross_return": str(gross),
            "benchmark_net_return": str(net),
            "strategy_gross_return": str(gross if action == "LONG" else rounded(Decimal(0))),
            "strategy_net_return": str(net if action == "LONG" else rounded(Decimal(0))),
            "input_snapshot_hash": snapshot_hash(base),
            "observation_snapshot_hash": snapshot_hash(observation),
        }
    result["content_hash"] = content_hash(result)
    return result
