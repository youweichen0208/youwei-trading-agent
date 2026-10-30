from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError
from yta_alpha.backtest import ExecutionCosts
from yta_alpha.backtest_report import BacktestSignal, build_report
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, rounded
from yta_alpha.models import DataMode, MarketBar


@pytest.fixture
def report_case(snapshot, calendar):
    base_day = snapshot.bars[-1].session
    days = [calendar.after(base_day, n) for n in range(12)]
    end = calendar.close_at(days[-1]) + timedelta(minutes=5)
    bars = tuple(
        MarketBar(
            session=day,
            open=str(100 + n * 10),
            high=str(100 + n * 10),
            low=str(100 + n * 10),
            close=str(100 + n * 10),
            volume=100,
            available_at=calendar.close_at(day),
            retrieved_at=end,
            revision="shared-synthetic-v1",
        )
        for n, day in enumerate(days)
    )
    observation = snapshot.model_copy(
        update={"bars": bars, "as_of": end, "splits": (), "actions_verified_through": days[-1]}
    )

    def signal(offset=0, action="LONG", holding=1):
        at = calendar.close_at(days[offset]) + timedelta(minutes=1)
        bar = bars[offset].model_copy(update={"retrieved_at": at})
        base = snapshot.model_copy(
            update={
                "bars": (bar,),
                "as_of": at,
                "splits": (),
                "actions_verified_through": days[offset],
            }
        )
        return BacktestSignal(
            signal_id=UUID(int=offset + 1),
            source_model_id="synthetic-test-model",
            source_signal_hash="a" * 64,
            base=base,
            action=action,
            holding_sessions=holding,
            decision_at=at,
        )

    def report(signals, **kwargs):
        options = dict(
            mode=DataMode.REPLAY,
            cutoff=end,
            costs=ExecutionCosts(commission_rate="0", slippage_rate="0"),
            calendar=calendar,
        )
        return build_report(
            tuple(signals), kwargs.pop("observation", observation), **(options | kwargs)
        )

    return signal, report, observation


def test_shared_snapshot_schedule_compounds_and_binds_inputs(report_case):
    signal, report, _ = report_case
    first, second = signal(), signal(1, "CASH")
    result = report([second, first])
    assert result == report([first, second])
    assert result["status"] == "COMPLETE"
    assert [r["signal_id"] for r in result["rows"]] == [str(first.signal_id), str(second.signal_id)]
    assert result["rows"][0]["exit_at"] == result["rows"][1]["entry_at"]
    assert result["computed_count"] == 2 and result["overlap_count"] == 0
    with localcontext() as context:
        context.prec = 50
        first_return = rounded(Decimal(120) / 110 - 1)
        expected = rounded(rounded(1 + first_return) * (1 + rounded(Decimal(130) / 120 - 1)))
        assert result["strategy_net_return"] == str(first_return)
        assert result["benchmark_net_return"] == str(rounded(expected - 1))
    assert result["maximum_realized_drawdown"] == "0"
    digest = result.pop("content_hash")
    assert digest == content_hash(result)
    with localcontext() as context:
        context.prec = 4
        assert report([first, second])["content_hash"] == digest


def test_overlap_is_explicit_and_cash_reserves_same_benchmark_window(report_case):
    signal, report, _ = report_case
    first, overlap, later = signal(0, "CASH", 5), signal(1), signal(5)
    result = report([later, overlap, first])
    assert result["status"] == "COMPLETE"
    assert result["signal_count"] == 3
    assert result["computed_count"] == 2 and result["overlap_count"] == 1
    skipped = result["rows"][1]
    assert skipped["status"] == "EXCLUDED_OVERLAP"
    assert skipped["overlaps_signal_id"] == str(first.signal_id)
    assert skipped["episode"] is None
    assert len(result["realized_equity_curve"]) == 2


def test_missing_evidence_keeps_later_episodes_but_no_fabricated_equity(report_case):
    signal, report, observation = report_case
    # Missing exit for first episode; later episode has all required observations.
    missing = observation.model_copy(
        update={"bars": tuple(b for i, b in enumerate(observation.bars) if i != 2)}
    )
    result = report([signal(), signal(3)], observation=missing)
    assert result["status"] == "INCOMPLETE"
    assert result["evidence_unavailable_count"] == result["computed_count"] == 1
    assert result["rows"][0]["error_code"] == "BACKTEST_MISSING_SESSION"
    assert result["rows"][1]["status"] == "COMPUTED"
    assert all(
        point["equity"] is None and point["benchmark_equity"] is None
        for point in result["realized_equity_curve"]
    )
    assert (
        result["strategy_net_return"]
        is result["benchmark_net_return"]
        is result["maximum_realized_drawdown"]
        is None
    )


def test_incomplete_reserved_window_does_not_admit_overlapping_signal(report_case):
    signal, report, observation = report_case
    missing = observation.model_copy(update={"bars": observation.bars[2:]})
    result = report([signal(0, holding=5), signal(1)], observation=missing)
    assert [r["status"] for r in result["rows"]] == ["EVIDENCE_UNAVAILABLE", "EXCLUDED_OVERLAP"]
    assert result["computed_count"] == 0 and result["strategy_net_return"] is None


def test_empty_does_not_claim_zero_return_and_duplicate_entries_are_rejected(report_case):
    signal, report, _ = report_case
    assert report([])["status"] == "EMPTY"
    assert report([])["strategy_net_return"] is None
    first = signal()
    for signals, code in (
        ([first, first], "BACKTEST_DUPLICATE_SIGNAL"),
        ([first, first.model_copy(update={"signal_id": UUID(int=99)})], "BACKTEST_AMBIGUOUS_ENTRY"),
    ):
        with pytest.raises(DomainError) as error:
            report(signals)
        assert error.value.code == code


def test_costs_drawdown_and_source_changes_are_bound(report_case):
    signal, report, _ = report_case
    first = signal()
    result = report([first], costs=ExecutionCosts(commission_rate="0.1", slippage_rate="0.1"))
    assert Decimal(result["strategy_net_return"]) < 0
    assert Decimal(result["maximum_realized_drawdown"]) == -Decimal(result["strategy_net_return"])
    assert result["content_hash"] != report([first])["content_hash"]
    assert (
        report([first.model_copy(update={"source_signal_hash": "b" * 64})])["content_hash"]
        != report([first])["content_hash"]
    )


def test_invalid_cohort_and_signal_instances_fail_closed(report_case):
    signal, report, observation = report_case
    with pytest.raises(DomainError):
        report([signal()], mode=DataMode.LIVE_RESEARCH)
    with pytest.raises(DomainError):
        report([signal()], observation=observation.model_copy(update={"data_mode": DataMode.DEMO}))
    with pytest.raises(ValidationError):
        report([signal().model_copy(update={"holding_sessions": True})])
    with pytest.raises(DomainError) as error:
        report([signal().model_copy(update={"decision_at": observation.as_of})])
    assert error.value.code == "BACKTEST_DECISION_WINDOW"


def test_unexpected_or_operational_errors_are_not_reclassified_as_missing_prices(
    report_case, monkeypatch
):
    from yta_alpha import backtest_report

    signal, report, _ = report_case

    def unavailable(*args, **kwargs):
        raise DomainError("STORAGE_UNAVAILABLE", "No storage", status=503)

    monkeypatch.setattr(backtest_report, "simulate_trade", unavailable)
    with pytest.raises(DomainError) as error:
        report([signal()])
    assert error.value.status == 503


def test_report_revalidation_keeps_invalid_boolean_volume_invalid(report_case):
    signal, report, observation = report_case
    bars = list(observation.bars)
    bars[1] = bars[1].model_copy(update={"volume": True})
    with pytest.raises(ValidationError):
        report([signal()], observation=observation.model_copy(update={"bars": tuple(bars)}))
