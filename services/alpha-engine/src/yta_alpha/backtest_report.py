"""Deterministic single-capital backtest schedule with explicit incomplete evidence."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictInt, field_validator

from yta_alpha.backtest import ExecutionCosts, simulate_trade
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, rounded, snapshot_hash, validate_snapshot
from yta_alpha.models import SHA256, Contract, DataMode, MarketSnapshot


class BacktestSignal(Contract):
    signal_id: UUID
    source_model_id: Annotated[str, Field(min_length=1, max_length=200)]
    source_signal_hash: SHA256
    base: MarketSnapshot
    action: Literal["LONG", "CASH"]
    holding_sessions: StrictInt
    decision_at: AwareDatetime

    @field_validator("holding_sessions")
    @classmethod
    def supported_holding(cls, value):
        if value not in (1, 3, 5, 10, 20):
            raise ValueError("Unsupported holding period")
        return value


# Evidence defects become visible rows. Operational/unexpected domain failures propagate.
EVIDENCE_ERRORS = frozenset(
    {
        "BACKTEST_MISSING_SESSION",
        "BACKTEST_EXECUTION_UNAVAILABLE",
        "NOT_DUE",
        "FUTURE_INFORMATION",
        "ACTIONS_UNVERIFIED",
        "BAR_NOT_FINAL",
        "NOT_TRADING_SESSION",
    }
)


def build_report(
    signals: tuple[BacktestSignal, ...],
    observation: MarketSnapshot,
    *,
    mode: DataMode,
    cutoff: datetime,
    costs: ExecutionCosts,
    calendar: TradingCalendar,
) -> dict:
    """Reserve one full capital slot per accepted signal, including CASH windows.

    Sort by entry time. Equal entry times are ambiguous and rejected, not chosen by
    performance or UUID. Later overlapping entries remain visible as excluded rows.
    Exit then re-entry at the same open is allowed with both episodes' full costs.
    Failed evidence never frees its reserved window or creates synthetic zero return.
    Once capital is unknown, later episodes remain independently inspectable but no
    final portfolio return or drawdown is claimed.
    """
    mode = DataMode(mode)
    if mode not in (DataMode.REPLAY, DataMode.DEMO):
        raise DomainError("BACKTEST_COHORT_MISMATCH", "Only isolated historical modes allowed")
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise DomainError("INVALID_TIME", "Report cutoff must include timezone")
    costs = ExecutionCosts.model_validate(costs.model_dump(mode="python"))
    observation = MarketSnapshot.model_validate(observation.model_dump(mode="python"))
    if observation.data_mode != mode:
        raise DomainError("BACKTEST_COHORT_MISMATCH", "Observation must match report mode")
    inputs = tuple(BacktestSignal.model_validate(s.model_dump(mode="python")) for s in signals)
    if len({s.signal_id for s in inputs}) != len(inputs):
        raise DomainError("BACKTEST_DUPLICATE_SIGNAL", "Signal IDs must be unique")
    entries = []
    for signal in inputs:
        if signal.base.data_mode != mode:
            raise DomainError("BACKTEST_COHORT_MISMATCH", "Every signal must match report mode")
        validate_snapshot(signal.base, calendar)
        entry = calendar.after(signal.base.bars[-1].session, 1)
        entry_at = calendar.open_at(entry)
        if not signal.base.as_of <= signal.decision_at < entry_at or signal.decision_at > cutoff:
            raise DomainError(
                "BACKTEST_DECISION_WINDOW", "Signal decision is outside its information window"
            )
        exit_at = calendar.open_at(calendar.after(entry, signal.holding_sessions))
        entries.append((entry_at, exit_at, signal))
    entries.sort(key=lambda item: item[0])
    if len({entry for entry, _, _ in entries}) != len(entries):
        raise DomainError("BACKTEST_AMBIGUOUS_ENTRY", "Multiple signals target the same entry open")
    rows, curve = [], []
    reserved_until = None
    reserved_by = None
    failed = completed = overlaps = 0
    capital_known = True
    with localcontext() as context:
        context.prec = 50
        equity = benchmark = peak = Decimal(1)
        max_drawdown = Decimal(0)
        for entry_at, exit_at, signal in entries:
            row = {
                "signal_id": str(signal.signal_id),
                "source_model_id": signal.source_model_id,
                "source_signal_hash": signal.source_signal_hash,
                "input_hash": content_hash(signal.model_dump(mode="json")),
                "input_snapshot_hash": snapshot_hash(signal.base),
                "observation_snapshot_hash": snapshot_hash(observation),
                "entry_at": entry_at.isoformat(),
                "exit_at": exit_at.isoformat(),
                "action": signal.action,
                "holding_sessions": signal.holding_sessions,
                "episode": None,
                "error_code": None,
                "overlaps_signal_id": None,
            }
            if reserved_until is not None and entry_at < reserved_until:
                overlaps += 1
                row.update(status="EXCLUDED_OVERLAP", overlaps_signal_id=reserved_by)
                rows.append(row)
                continue
            reserved_until, reserved_by = exit_at, str(signal.signal_id)
            try:
                episode = simulate_trade(
                    signal.base,
                    observation,
                    action=signal.action,
                    holding_sessions=signal.holding_sessions,
                    costs=costs,
                    decision_at=signal.decision_at,
                    cutoff=cutoff,
                    calendar=calendar,
                )
            except DomainError as error:
                if error.code not in EVIDENCE_ERRORS:
                    raise
                row.update(status="EVIDENCE_UNAVAILABLE", error_code=error.code)
                failed += 1
                capital_known = False
            else:
                row.update(status="COMPUTED", episode=episode)
                completed += 1
                if capital_known:
                    equity = rounded(equity * (1 + Decimal(episode["strategy_net_return"])))
                    benchmark = rounded(benchmark * (1 + Decimal(episode["benchmark_net_return"])))
                    peak = max(peak, equity)
                    max_drawdown = max(max_drawdown, rounded(1 - equity / peak))
            curve.append(
                {
                    "signal_id": str(signal.signal_id),
                    "at": exit_at.isoformat(),
                    "equity": str(equity) if capital_known else None,
                    "benchmark_equity": str(benchmark) if capital_known else None,
                }
            )
            rows.append(row)
        complete = bool(entries) and failed == 0
        result = {
            "schema_version": "next-open-backtest-report-v1",
            "metrics_kind": "BACKTEST",
            "qualification": "UNVERIFIED",
            "signal_verification": "caller-supplied-not-replayed",
            "observation_snapshot_hash": snapshot_hash(observation),
            "data_mode": mode.value,
            "symbol": "NVDA",
            "calendar_version": calendar.version,
            "cutoff": cutoff.isoformat(),
            "scheduling_policy": "single-capital-first-entry-reserve-cash-v1",
            "equity_policy": "compound-net-returns-round-half-even-12-at-each-exit",
            "benchmark": "same-scheduled-windows-long-with-same-costs",
            "status": "COMPLETE" if complete else "INCOMPLETE" if entries else "EMPTY",
            "costs": costs.model_dump(mode="json"),
            "signal_count": len(entries),
            "computed_count": completed,
            "evidence_unavailable_count": failed,
            "overlap_count": overlaps,
            "rows": rows,
            "realized_equity_curve": curve,
            "strategy_net_return": str(rounded(equity - 1)) if complete else None,
            "benchmark_net_return": str(rounded(benchmark - 1)) if complete else None,
            "maximum_realized_drawdown": str(max_drawdown) if complete else None,
        }
    result["content_hash"] = content_hash(result)
    return result
