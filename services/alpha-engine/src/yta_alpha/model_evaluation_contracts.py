"""Exact-text report transport: timestamps and decimals must retain hashed spelling."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StrictInt, model_validator

from yta_alpha.models import SHA256, Contract, DataMode, Direction


def aware_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp requires an explicit offset")
    return value


def session_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use an ISO session date")
    return value


Timestamp = Annotated[
    str, Field(json_schema_extra={"format": "date-time"}), AfterValidator(aware_timestamp)
]
SessionDate = Annotated[
    str, Field(json_schema_extra={"format": "date"}), AfterValidator(session_date)
]
ReturnString = Annotated[str, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")]
NonnegativeString = Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
ModelId = Annotated[str, Field(pattern=r"^lightgbm-experimental-[a-f0-9]{64}$")]
CalibrationId = Annotated[str, Field(pattern=r"^calibration-experimental-[a-f0-9]{64}$")]


class PriceMetrics(Contract):
    sample_count: Annotated[StrictInt, Field(ge=0)]
    mae: NonnegativeString | None
    direction_accuracy: NonnegativeString | None


class PriceComparisonMetrics(Contract):
    model: PriceMetrics
    zero_return: PriceMetrics = Field(alias="zero-return-v1")
    momentum: PriceMetrics | None = Field(alias="momentum20-v1")


class EvaluationCohort(Contract):
    label_ids: tuple[SHA256, ...]
    cutoff: Timestamp
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    calendar_version: str


class EvaluationManifestRow(Contract):
    label_id: SHA256
    base_session: SessionDate
    target_session: SessionDate
    feature_as_of: Timestamp
    label_available_at: Timestamp
    base_snapshot_hash: SHA256
    observation_snapshot_hash: SHA256
    actual_return: ReturnString
    actual_direction: Direction
    model_expected_return: ReturnString
    momentum_expected_return: ReturnString | None
    zero_expected_return: Literal["0.000000000000"]


class EvaluationCodeHashes(Contract):
    price_evaluation: SHA256
    forecast: SHA256
    training: SHA256
    calibration: SHA256


class ExperimentalPriceReport(Contract):
    schema_version: Literal["experimental-price-evaluation-v1"]
    status: Literal["EXPERIMENTAL"]
    qualification: Literal["UNVERIFIED"]
    scope: Literal["POST_CALIBRATION_HISTORICAL_EVALUATION"]
    model_id: ModelId
    model_sha256: SHA256
    calibration_id: CalibrationId
    calibration_content_hash: SHA256
    protocol_version: Literal["eod-price-v1"]
    return_basis: Literal["split-only-price-return"]
    cohort: EvaluationCohort
    cohort_hash: SHA256
    metrics: PriceComparisonMetrics
    momentum_unavailable_reason: Literal["FIVE_SESSION_ONLY"] | None
    manifest: tuple[EvaluationManifestRow, ...]
    code_sha256: EvaluationCodeHashes
    content_hash: SHA256


EvaluationId = Annotated[str, Field(pattern=r"^model-evaluation-experimental-[a-f0-9]{64}$")]


class EvaluationComparisonRequest(Contract):
    evaluation_ids: Annotated[tuple[EvaluationId, ...], Field(min_length=2, max_length=10)]

    @model_validator(mode="after")
    def distinct_ids(self):
        if len(set(self.evaluation_ids)) != len(self.evaluation_ids):
            raise ValueError("Select distinct registered reports")
        return self


class ExperimentalReportComparison(Contract):
    schema_version: Literal["experimental-report-comparison-v1"]
    status: Literal["EXPERIMENTAL"]
    qualification: Literal["UNVERIFIED"]
    evaluation_ids: Annotated[tuple[EvaluationId, ...], Field(min_length=2, max_length=10)]
    cohort_hash: SHA256
    reports: Annotated[tuple[ExperimentalPriceReport, ...], Field(min_length=2, max_length=10)]
