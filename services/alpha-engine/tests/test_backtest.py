from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError
from yta_alpha.backtest import ExecutionCosts, simulate_trade
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import DataMode, MarketBar, Split


def observation(snapshot, calendar, *, split=False, holding=5):
    entry = calendar.after(snapshot.bars[-1].session, 1)
    exit_session = calendar.after(entry, holding)
    at = calendar.close_at(exit_session) + timedelta(minutes=5)
    bars = tuple(
        MarketBar(
            session=session,
            open="55" if split and session == exit_session else "100",
            high="100",
            low="50",
            close="100",
            volume=100,
            available_at=calendar.close_at(session),
            retrieved_at=at,
            revision="synthetic-open",
        )
        for session in calendar.sessions(entry, exit_session)
    )
    return snapshot.model_copy(
        update={
            "as_of": at,
            "bars": bars,
            "actions_verified_through": exit_session,
            "splits": (
                Split(
                    effective_session=exit_session,
                    new_shares_per_old=Decimal(2),
                    available_at=at,
                    source_id="synthetic-split",
                ),
            )
            if split
            else (),
        }
    )


def run(snapshot, calendar, observed=None, **kwargs):
    observed = observed or observation(snapshot, calendar)
    args = dict(
        action="LONG",
        holding_sessions=5,
        costs=ExecutionCosts(commission_rate="0.001", slippage_rate="0.01"),
        decision_at=snapshot.as_of,
        cutoff=observed.as_of,
        calendar=calendar,
    )
    return simulate_trade(snapshot, observed, **(args | kwargs))


def test_next_open_costs_split_benchmark_and_content_identity(snapshot, calendar):
    observed = observation(snapshot, calendar, split=True)
    result = run(snapshot, calendar, observed)
    assert result["entry_session"] == "2024-11-25"
    assert result["exit_session"] == "2024-12-03"  # Thanksgiving/weekend are not holding sessions.
    assert result["entry_open"] == "100" and result["exit_open"] == "55"
    assert result["benchmark_gross_return"] == "0.100000000000"
    with localcontext() as context:
        context.prec = 50
        expected = (
            Decimal(110) * Decimal("0.99") * Decimal("0.999") / (Decimal(101) * Decimal("1.001"))
            - 1
        ).quantize(Decimal("0.000000000001"))
    assert result["strategy_net_return"] == str(expected)
    assert result["benchmark_net_return"] == str(expected)
    digest = result.pop("content_hash")
    assert digest == content_hash(result)
    with localcontext() as context:
        context.prec = 4
        assert run(snapshot, calendar, observed)["content_hash"] == digest


def test_cash_keeps_same_benchmark_and_zero_strategy_return(snapshot, calendar):
    result = run(snapshot, calendar, action="CASH")
    assert result["strategy_net_return"] == result["strategy_gross_return"] == "0E-12"
    assert Decimal(result["benchmark_net_return"]) < 0
    free = run(snapshot, calendar, costs=ExecutionCosts(commission_rate="0", slippage_rate="0"))
    assert Decimal(free["strategy_net_return"]) == 0


@pytest.mark.parametrize("value", [True, 0.1, "-0.1", "1", "NaN", "Infinity"])
def test_invalid_or_binary_cost_rates_rejected(value):
    with pytest.raises(ValidationError):
        ExecutionCosts(commission_rate=value, slippage_rate="0")


@pytest.mark.parametrize("which", ["entry", "intermediate", "exit", "zero_volume"])
def test_no_fill_or_roll_forward_for_missing_or_zero_volume_sessions(snapshot, calendar, which):
    observed = observation(snapshot, calendar)
    bars = list(observed.bars)
    if which == "zero_volume":
        bars[2] = bars[2].model_copy(update={"volume": 0})
    else:
        bars.pop({"entry": 0, "intermediate": 2, "exit": -1}[which])
    with pytest.raises(DomainError) as error:
        run(snapshot, calendar, observed.model_copy(update={"bars": tuple(bars)}))
    assert error.value.code == (
        "BACKTEST_EXECUTION_UNAVAILABLE" if which == "zero_volume" else "BACKTEST_MISSING_SESSION"
    )


def test_decision_cannot_use_entry_open_or_future_information(snapshot, calendar):
    observed = observation(snapshot, calendar)
    entry = calendar.after(snapshot.bars[-1].session, 1)
    for at in (snapshot.as_of - timedelta(seconds=1), calendar.open_at(entry)):
        with pytest.raises(DomainError, match="Decision"):
            run(snapshot, calendar, observed, decision_at=at)
    with pytest.raises(DomainError) as error:
        run(snapshot, calendar, observed, cutoff=observed.as_of - timedelta(seconds=1))
    assert error.value.code == "FUTURE_INFORMATION"
    with pytest.raises(DomainError) as error:
        run(snapshot, calendar, observed, decision_at=snapshot.as_of.replace(tzinfo=None))
    assert error.value.code == "INVALID_TIME"


def test_modes_actions_horizons_and_model_copy_revalidated(snapshot, calendar):
    with pytest.raises(DomainError) as error:
        run(snapshot.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), calendar)
    assert error.value.code == "BACKTEST_COHORT_MISMATCH"
    for args in ({"holding_sessions": True}, {"holding_sessions": 2}, {"action": "SHORT"}):
        with pytest.raises(DomainError) as error:
            run(snapshot, calendar, **args)
        assert error.value.code == "INVALID_BACKTEST_REQUEST"
    with pytest.raises(ValidationError):
        run(
            snapshot,
            calendar,
            costs=ExecutionCosts(commission_rate="0", slippage_rate="0").model_copy(
                update={"commission_rate": Decimal(2)}
            ),
        )


@pytest.mark.parametrize("holding", [1, 3, 5, 10, 20])
def test_all_holding_periods_use_exchange_sessions(snapshot, calendar, holding):
    observed = observation(snapshot, calendar, holding=holding)
    result = run(snapshot, calendar, observed, holding_sessions=holding)
    expected = calendar.after(snapshot.bars[-1].session, holding + 1)
    assert result["exit_session"] == expected.isoformat()
    assert result["exit_at"] == calendar.open_at(expected).isoformat()


def test_split_on_entry_does_not_double_count_and_non_session_action_rejected(snapshot, calendar):
    observed = observation(snapshot, calendar, split=True)
    split = observed.splits[0].model_copy(update={"effective_session": observed.bars[0].session})
    result = run(snapshot, calendar, observed.model_copy(update={"splits": (split,)}))
    assert result["split_factor"] == "1"
    assert result["benchmark_gross_return"] == "-0.450000000000"
    split = split.model_copy(
        update={"effective_session": observed.bars[0].session - timedelta(days=1)}
    )
    with pytest.raises(DomainError) as error:
        run(snapshot, calendar, observed.model_copy(update={"splits": (split,)}))
    assert error.value.code == "NOT_TRADING_SESSION"


def test_revalidation_does_not_convert_boolean_volume_into_a_tradable_integer(snapshot, calendar):
    observed = observation(snapshot, calendar)
    bars = list(observed.bars)
    bars[0] = bars[0].model_copy(update={"volume": True})
    with pytest.raises(ValidationError):
        run(snapshot, calendar, observed.model_copy(update={"bars": tuple(bars)}))
