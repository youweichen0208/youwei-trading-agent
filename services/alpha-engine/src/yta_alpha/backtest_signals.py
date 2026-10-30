"""Read-only registered inference provenance for historical backtest signals."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field

from yta_alpha.backtest import ExecutionCosts
from yta_alpha.backtest_report import BacktestSignal, build_report
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.experimental_predictions import ExperimentalRequest, experimental_predict
from yta_alpha.forecast import content_hash
from yta_alpha.model_registry import ModelRegistry
from yta_alpha.models import Contract, DataMode, MarketSnapshot


class RegisteredBacktestRequest(Contract):
    signal_id: UUID
    model_id: Annotated[str, Field(pattern=r"^lightgbm-experimental-[a-f0-9]{64}$")]
    snapshot: MarketSnapshot
    decision_at: AwareDatetime


class BacktestSignalService:
    def __init__(self, url: str, calendar: TradingCalendar):
        self.models = ModelRegistry(url)
        self.calendar = calendar

    def evaluate(self, request: RegisteredBacktestRequest, *, mode: DataMode, cutoff: datetime):
        request = RegisteredBacktestRequest.model_validate(request.model_dump(mode="python"))
        mode = DataMode(mode)
        if mode not in (DataMode.REPLAY, DataMode.DEMO) or request.snapshot.data_mode != mode:
            raise DomainError(
                "BACKTEST_COHORT_MISMATCH", "Signals require matching historical mode"
            )
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise DomainError("INVALID_TIME", "Backtest cutoff must include timezone")
        entry = self.calendar.after(request.snapshot.bars[-1].session, 1)
        if (
            not request.snapshot.as_of <= request.decision_at < self.calendar.open_at(entry)
            or request.decision_at > cutoff
        ):
            raise DomainError(
                "BACKTEST_DECISION_WINDOW", "Decision is outside its information window"
            )
        registration, registered_at = self.models.get_with_timestamp(request.model_id)
        if registration.artifact.data_mode != mode:
            raise DomainError("BACKTEST_COHORT_MISMATCH", "Registered model mode differs")
        prediction = experimental_predict(
            self.models,
            request.model_id,
            ExperimentalRequest(
                snapshot=request.snapshot, horizon_sessions=registration.artifact.horizon_sessions
            ),
            calendar=self.calendar,
            now=request.decision_at,
            replay=True,
        )
        action = "LONG" if prediction.expected_return > 0 else "CASH"
        source = {
            "schema_version": "registered-backtest-signal-v1",
            "signal_id": str(request.signal_id),
            "model_id": request.model_id,
            "registration_content_hash": content_hash(registration.model_dump(mode="json")),
            "artifact_file_sha256": registration.artifact_file_sha256,
            "registered_at": registered_at.isoformat(),
            "model_availability_policy": "historical-training-cutoff-registration-time-disclosed",
            "qualification": "UNVERIFIED",
            "training_evidence_verification": "not-retrained",
            "mapping_policy": "positive-return-long-otherwise-cash-v1",
            "holding_policy": "holding-sessions-equal-model-horizon-after-next-open-entry",
            "decision_at": request.decision_at.isoformat(),
            "action": action,
            "prediction": prediction.model_dump(mode="json"),
        }
        digest = content_hash(source)
        source["content_hash"] = digest
        signal = BacktestSignal(
            signal_id=request.signal_id,
            source_model_id=request.model_id,
            source_signal_hash=digest,
            base=request.snapshot,
            action=action,
            holding_sessions=prediction.horizon_sessions,
            decision_at=request.decision_at,
        )
        return signal, source

    def report(
        self,
        requests: tuple[RegisteredBacktestRequest, ...],
        observation: MarketSnapshot,
        *,
        mode: DataMode,
        cutoff: datetime,
        costs: ExecutionCosts,
    ):
        # Every signal, including eventual overlaps, is replayed before scheduling.
        pairs = tuple(self.evaluate(request, mode=mode, cutoff=cutoff) for request in requests)
        result = build_report(
            tuple(signal for signal, _ in pairs),
            observation,
            mode=mode,
            cutoff=cutoff,
            costs=costs,
            calendar=self.calendar,
        )
        sources = {str(signal.signal_id): source for signal, source in pairs}
        result.pop("content_hash")
        result["schema_version"] = "registered-next-open-backtest-report-v2"
        result["signal_verification"] = "registered-native-inference-replayed"
        result["signal_sources"] = [sources[row["signal_id"]] for row in result["rows"]]
        result["content_hash"] = content_hash(result)
        return result
