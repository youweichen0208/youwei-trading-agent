"""Exact-text transport of replayed experimental probability evidence."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StrictBool, StrictInt, model_validator

from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import CalibrationId, ModelId, Timestamp
from yta_alpha.models import SHA256, Contract, DataMode


def probability(value: str) -> str:
    if not Decimal(0) <= Decimal(value) <= Decimal(1):
        raise ValueError("Probability must be between zero and one")
    return value


# Existing immutable reports use str(Decimal), including zero as 0E-12.
CalibrationDecimal = Annotated[
    str, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,6})?$")
]
ProbabilityString = Annotated[CalibrationDecimal, AfterValidator(probability)]
Count = Annotated[StrictInt, Field(ge=0)]
Target = Annotated[StrictInt, Field(ge=0, le=1)]


class ReliabilityBin(Contract):
    lower: ProbabilityString
    upper: ProbabilityString
    upper_inclusive: StrictBool
    count: Count
    mean_probability: ProbabilityString | None
    up_frequency: ProbabilityString | None

    @model_validator(mode="after")
    def consistent(self):
        if Decimal(self.lower) >= Decimal(self.upper) or (
            (self.mean_probability is None or self.up_frequency is None)
            if self.count
            else (self.mean_probability is not None or self.up_frequency is not None)
        ):
            raise ValueError("Reliability bin count or interval differs")
        return self


class ReliabilityReport(Contract):
    sample_count: Count
    up_count: Count
    bins: tuple[ReliabilityBin, ...]
    brier: ProbabilityString | None
    ece: ProbabilityString | None

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.up_count > self.sample_count
            or sum(row.count for row in self.bins) != self.sample_count
            or len(self.bins) != 10
            or any(
                Decimal(row.lower) != Decimal(i) / 10
                or Decimal(row.upper) != Decimal(i + 1) / 10
                or row.upper_inclusive != (i == 9)
                for i, row in enumerate(self.bins)
            )
            or (
                (self.brier is None or self.ece is None)
                if self.sample_count
                else (self.brier is not None or self.ece is not None)
            )
        ):
            raise ValueError("Reliability summary differs")
        return self


class CalibrationReport(Contract):
    schema_version: Literal["calibration-report-v1"]
    status: Literal["EXPERIMENTAL"]
    qualification: Literal["UNVERIFIED"]
    method: Literal["isotonic-linear-decimal-v1"]
    positive_class: Literal["UP; FLAT and DOWN are 0"]
    out_of_bounds: Literal["endpoint_probability"]
    model_artifact_hash: SHA256
    model_sha256: SHA256
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    calendar_version: str
    calibration_ids: tuple[SHA256, ...]
    evaluation_ids: tuple[SHA256, ...]
    calibration_available_at: Timestamp
    cutoff: Timestamp
    embargo_sessions: Count
    knots: tuple[tuple[CalibrationDecimal, ProbabilityString], ...]
    probabilities: tuple[ProbabilityString, ...]
    calibration_scores: tuple[CalibrationDecimal, ...]
    calibration_targets: tuple[Target, ...]
    evaluation_targets: tuple[Target, ...]
    code_sha256: SHA256
    reliability: ReliabilityReport

    @model_validator(mode="after")
    def aligned_windows(self):
        if (
            not self.calibration_ids
            or not self.evaluation_ids
            or len(set(self.calibration_ids)) != len(self.calibration_ids)
            or len(set(self.evaluation_ids)) != len(self.evaluation_ids)
            or set(self.calibration_ids) & set(self.evaluation_ids)
            or len(self.calibration_scores) != len(self.calibration_ids)
            or len(self.calibration_targets) != len(self.calibration_ids)
            or len(self.probabilities) != len(self.evaluation_ids)
            or len(self.evaluation_targets) != len(self.evaluation_ids)
            or self.reliability.sample_count != len(self.evaluation_ids)
            or self.reliability.up_count != sum(self.evaluation_targets)
            or not self.knots
            or any(
                Decimal(a[0]) >= Decimal(b[0]) or Decimal(a[1]) > Decimal(b[1])
                for a, b in zip(self.knots, self.knots[1:], strict=False)
            )
        ):
            raise ValueError("Calibration windows or evidence lengths differ")
        return self


class CalibrationReportResponse(Contract):
    id: CalibrationId
    model_id: ModelId
    registered_at: Timestamp
    content_hash: SHA256
    report_hash: SHA256
    report: CalibrationReport

    @model_validator(mode="after")
    def identity(self):
        if (
            self.id != "calibration-experimental-" + self.content_hash
            or self.report_hash != content_hash(self.report.model_dump(mode="json"))
        ):
            raise ValueError("Calibration response identity differs")
        return self
