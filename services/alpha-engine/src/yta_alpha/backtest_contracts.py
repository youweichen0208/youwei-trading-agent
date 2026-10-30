"""Exact-text wire projections for fully replayed registered backtests."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, StrictInt, model_validator

from yta_alpha.backtest_registry import CODE_FILES
from yta_alpha.calibration_contracts import CalibrationDecimal
from yta_alpha.forecast import content_hash, rounded
from yta_alpha.model_evaluation_contracts import ModelId, SessionDate, Timestamp
from yta_alpha.models import SHA256, Contract


def rate(value):
    if not 0 <= Decimal(value) < 1:
        raise ValueError("Cost must be a per-side fraction in [0,1)")
    return value


def positive(value):
    if Decimal(value) <= 0:
        raise ValueError("Positive price/factor required")
    return value


def return_value(value):
    if Decimal(value) < -1:
        raise ValueError("Return cannot be below -1")
    return value


def holding(value):
    if value not in (1, 3, 5, 10, 20):
        raise ValueError("Unsupported holding period")
    return value


RateText = Annotated[CalibrationDecimal, AfterValidator(rate)]
PositiveText = Annotated[CalibrationDecimal, AfterValidator(positive)]
ReturnText = Annotated[CalibrationDecimal, AfterValidator(return_value)]
Holding = Annotated[StrictInt, AfterValidator(holding)]
Count = Annotated[StrictInt, Field(ge=0)]
BacktestId = Annotated[str, Field(pattern=r"^backtest-report-[a-f0-9]{64}$")]
HistoricalMode = Literal["REPLAY", "DEMO"]


def check_hash(value):
    body = value.model_dump(mode="json")
    expected = body.pop("content_hash")
    if content_hash(body) != expected:
        raise ValueError("Backtest content hash differs")


class CostRecord(Contract):
    commission_rate: RateText
    slippage_rate: RateText


class InferenceRecord(Contract):
    status: Literal["EXPERIMENTAL"]
    model_kind: Literal["TRAINED"]
    model_id: ModelId
    model_sha256: SHA256
    data_snapshot_hash: SHA256
    data_mode: HistoricalMode
    horizon_sessions: Holding
    as_of: Timestamp
    training_cutoff: Timestamp
    base_session: SessionDate
    target_session: SessionDate
    target_close_at: Timestamp
    calendar_version: str
    expected_return: ReturnText
    direction: Literal["UP", "DOWN", "FLAT"]
    probability_up: None
    protocol_version: Literal["eod-price-v1"]
    return_basis: Literal["split-only-price-return"]

    @model_validator(mode="after")
    def consistent(self):
        number = Decimal(self.expected_return)
        expected = "UP" if number > 0 else "DOWN" if number < 0 else "FLAT"
        if self.direction != expected or datetime.fromisoformat(
            self.training_cutoff
        ) > datetime.fromisoformat(self.as_of):
            raise ValueError("Inference direction or training cutoff differs")
        return self


class SignalSource(Contract):
    schema_version: Literal["registered-backtest-signal-v1"]
    signal_id: UUID
    model_id: ModelId
    registration_content_hash: SHA256
    artifact_file_sha256: SHA256
    registered_at: Timestamp
    model_availability_policy: Literal["historical-training-cutoff-registration-time-disclosed"]
    qualification: Literal["UNVERIFIED"]
    training_evidence_verification: Literal["not-retrained"]
    mapping_policy: Literal["positive-return-long-otherwise-cash-v1"]
    holding_policy: Literal["holding-sessions-equal-model-horizon-after-next-open-entry"]
    decision_at: Timestamp
    action: Literal["LONG", "CASH"]
    prediction: InferenceRecord
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent(self):
        check_hash(self)
        if (
            self.model_id != "lightgbm-experimental-" + self.registration_content_hash
            or self.prediction.model_id != self.model_id
            or self.action != ("LONG" if Decimal(self.prediction.expected_return) > 0 else "CASH")
            or datetime.fromisoformat(self.decision_at)
            < datetime.fromisoformat(self.prediction.as_of)
        ):
            raise ValueError("Signal source identity or action differs")
        return self


class EpisodeRecord(Contract):
    schema_version: Literal["next-open-backtest-episode-v1"]
    metrics_kind: Literal["BACKTEST"]
    qualification: Literal["UNVERIFIED"]
    execution_assumption: Literal["historical-open-proxy-no-fill-guarantee"]
    return_basis: Literal["split-only-price-return-excluding-dividends"]
    symbol: Literal["NVDA"]
    data_mode: HistoricalMode
    calendar_version: str
    action: Literal["LONG", "CASH"]
    holding_sessions: Holding
    decision_at: Timestamp
    cutoff: Timestamp
    entry_session: SessionDate
    exit_session: SessionDate
    entry_at: Timestamp
    exit_at: Timestamp
    entry_open: PositiveText
    exit_open: PositiveText
    split_factor: PositiveText
    costs: CostRecord
    benchmark_gross_return: ReturnText
    benchmark_net_return: ReturnText
    strategy_gross_return: ReturnText
    strategy_net_return: ReturnText
    input_snapshot_hash: SHA256
    observation_snapshot_hash: SHA256
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent(self):
        check_hash(self)
        if (
            not datetime.fromisoformat(self.decision_at)
            < datetime.fromisoformat(self.entry_at)
            < datetime.fromisoformat(self.exit_at)
            <= datetime.fromisoformat(self.cutoff)
        ):
            raise ValueError("Execution timestamps differ")
        with localcontext() as context:
            context.prec = 50
            first, last, factor = map(Decimal, (self.entry_open, self.exit_open, self.split_factor))
            fee, slip = Decimal(self.costs.commission_rate), Decimal(self.costs.slippage_rate)
            gross = rounded(last * factor / first - 1)
            net = rounded(
                last * factor * (1 - slip) * (1 - fee) / (first * (1 + slip) * (1 + fee)) - 1
            )
            if (
                Decimal(self.benchmark_gross_return) != gross
                or Decimal(self.benchmark_net_return) != net
                or Decimal(self.strategy_gross_return) != (gross if self.action == "LONG" else 0)
                or Decimal(self.strategy_net_return) != (net if self.action == "LONG" else 0)
            ):
                raise ValueError("Execution costs or returns differ")
        return self


class BacktestRow(Contract):
    signal_id: UUID
    source_model_id: ModelId
    source_signal_hash: SHA256
    input_hash: SHA256
    input_snapshot_hash: SHA256
    observation_snapshot_hash: SHA256
    entry_at: Timestamp
    exit_at: Timestamp
    action: Literal["LONG", "CASH"]
    holding_sessions: Holding
    episode: EpisodeRecord | None
    error_code: (
        Literal[
            "BACKTEST_MISSING_SESSION",
            "BACKTEST_EXECUTION_UNAVAILABLE",
            "NOT_DUE",
            "FUTURE_INFORMATION",
            "ACTIONS_UNVERIFIED",
            "BAR_NOT_FINAL",
            "NOT_TRADING_SESSION",
        ]
        | None
    )
    overlaps_signal_id: UUID | None
    status: Literal["COMPUTED", "EVIDENCE_UNAVAILABLE", "EXCLUDED_OVERLAP"]

    @model_validator(mode="after")
    def consistent(self):
        if (
            (self.episode is not None) != (self.status == "COMPUTED")
            or (self.error_code is not None) != (self.status == "EVIDENCE_UNAVAILABLE")
            or (self.overlaps_signal_id is not None) != (self.status == "EXCLUDED_OVERLAP")
            or datetime.fromisoformat(self.entry_at) >= datetime.fromisoformat(self.exit_at)
        ):
            raise ValueError("Row state differs")
        return self


class EquityPoint(Contract):
    signal_id: UUID
    at: Timestamp
    equity: CalibrationDecimal | None
    benchmark_equity: CalibrationDecimal | None


class BacktestReport(Contract):
    schema_version: Literal["registered-next-open-backtest-report-v2"]
    metrics_kind: Literal["BACKTEST"]
    qualification: Literal["UNVERIFIED"]
    signal_verification: Literal["registered-native-inference-replayed"]
    observation_snapshot_hash: SHA256
    data_mode: HistoricalMode
    symbol: Literal["NVDA"]
    calendar_version: str
    cutoff: Timestamp
    scheduling_policy: Literal["single-capital-first-entry-reserve-cash-v1"]
    equity_policy: Literal["compound-net-returns-round-half-even-12-at-each-exit"]
    benchmark: Literal["same-scheduled-windows-long-with-same-costs"]
    status: Literal["COMPLETE", "INCOMPLETE"]
    costs: CostRecord
    signal_count: Annotated[StrictInt, Field(ge=1, le=500)]
    computed_count: Count
    evidence_unavailable_count: Count
    overlap_count: Count
    rows: Annotated[tuple[BacktestRow, ...], Field(min_length=1, max_length=500)]
    realized_equity_curve: Annotated[tuple[EquityPoint, ...], Field(max_length=500)]
    strategy_net_return: ReturnText | None
    benchmark_net_return: ReturnText | None
    maximum_realized_drawdown: CalibrationDecimal | None
    signal_sources: Annotated[tuple[SignalSource, ...], Field(min_length=1, max_length=500)]
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent(self):
        check_hash(self)
        counts = [
            sum(row.status == state for row in self.rows)
            for state in ("COMPUTED", "EVIDENCE_UNAVAILABLE", "EXCLUDED_OVERLAP")
        ]
        if (
            len(self.rows) != self.signal_count
            or len(self.signal_sources) != self.signal_count
            or len({row.signal_id for row in self.rows}) != self.signal_count
            or counts != [self.computed_count, self.evidence_unavailable_count, self.overlap_count]
        ):
            raise ValueError("Report counts or signal identities differ")
        reserve = previous_entry = reserve_id = None
        curve = []
        known = True
        with localcontext() as context:
            context.prec = 50
            equity = benchmark = peak = Decimal(1)
            drawdown = Decimal(0)
            for row, source in zip(self.rows, self.signal_sources, strict=True):
                entry, exit_at = (
                    datetime.fromisoformat(row.entry_at),
                    datetime.fromisoformat(row.exit_at),
                )
                if previous_entry is not None and entry <= previous_entry:
                    raise ValueError("Entry order differs")
                previous_entry = entry
                if (
                    source.signal_id != row.signal_id
                    or source.model_id != row.source_model_id
                    or source.content_hash != row.source_signal_hash
                    or source.action != row.action
                    or source.prediction.horizon_sessions != row.holding_sessions
                    or source.prediction.data_snapshot_hash != row.input_snapshot_hash
                    or source.prediction.data_mode != self.data_mode
                    or source.prediction.calendar_version != self.calendar_version
                    or row.observation_snapshot_hash != self.observation_snapshot_hash
                    or datetime.fromisoformat(source.decision_at) >= entry
                    or datetime.fromisoformat(source.decision_at)
                    > datetime.fromisoformat(self.cutoff)
                ):
                    raise ValueError("Signal source binding differs")
                overlaps = reserve is not None and entry < reserve
                if overlaps != (row.status == "EXCLUDED_OVERLAP") or (
                    overlaps and row.overlaps_signal_id != reserve_id
                ):
                    raise ValueError("Capital reservation differs")
                if overlaps:
                    continue
                reserve, reserve_id = exit_at, row.signal_id
                if row.episode is None:
                    known = False
                else:
                    episode = row.episode
                    if (
                        episode.entry_at != row.entry_at
                        or episode.exit_at != row.exit_at
                        or episode.action != row.action
                        or episode.holding_sessions != row.holding_sessions
                        or episode.decision_at != source.decision_at
                        or episode.cutoff != self.cutoff
                        or episode.data_mode != self.data_mode
                        or episode.calendar_version != self.calendar_version
                        or episode.input_snapshot_hash != row.input_snapshot_hash
                        or episode.observation_snapshot_hash != self.observation_snapshot_hash
                        or episode.costs != self.costs
                    ):
                        raise ValueError("Episode scope differs")
                    if known:
                        equity = rounded(equity * (1 + Decimal(episode.strategy_net_return)))
                        benchmark = rounded(benchmark * (1 + Decimal(episode.benchmark_net_return)))
                        peak = max(peak, equity)
                        drawdown = max(drawdown, rounded(1 - equity / peak))
                curve.append(
                    {
                        "signal_id": str(row.signal_id),
                        "at": row.exit_at,
                        "equity": str(equity) if known else None,
                        "benchmark_equity": str(benchmark) if known else None,
                    }
                )
            expected = (
                (str(rounded(equity - 1)), str(rounded(benchmark - 1)), str(drawdown))
                if known
                else (None, None, None)
            )
            if (
                self.status != ("COMPLETE" if known else "INCOMPLETE")
                or (
                    self.strategy_net_return,
                    self.benchmark_net_return,
                    self.maximum_realized_drawdown,
                )
                != expected
                or [point.model_dump(mode="json") for point in self.realized_equity_curve] != curve
            ):
                raise ValueError("Equity curve or final metrics differ")
        return self


class BacktestResponse(Contract):
    id: BacktestId
    content_hash: SHA256
    input_hash: SHA256
    code_sha256: dict[str, SHA256]
    report: BacktestReport

    @model_validator(mode="after")
    def consistent(self):
        if self.id != "backtest-report-" + self.content_hash or set(self.code_sha256) != set(
            CODE_FILES
        ):
            raise ValueError("Registration identity differs")
        return self
