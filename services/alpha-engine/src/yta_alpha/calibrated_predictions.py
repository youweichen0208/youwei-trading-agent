"""Explicitly selected experimental calibration, with point-in-time availability gates."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from yta_alpha.calibrate_cli import CalibrationInput
from yta_alpha.calibration import calibrated_probability
from yta_alpha.calibration_registry import CalibrationRegistry
from yta_alpha.errors import DomainError
from yta_alpha.experimental_predictions import (
    ExperimentalPrediction,
    ExperimentalRequest,
    experimental_predict,
)


class CalibratedExperimentalPrediction(ExperimentalPrediction):
    probability_up: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
    calibration_id: str
    probability_qualification: Literal["UNVERIFIED"] = "UNVERIFIED"
    calibration_available_at: AwareDatetime


def calibrated_predict(
    registry: CalibrationRegistry,
    model_id: str,
    calibration_id: str,
    body: ExperimentalRequest,
    *,
    now: datetime,
    replay: bool,
):
    payload, registered_at = registry.get_with_timestamp(calibration_id)
    if payload["model_id"] != model_id:
        raise DomainError("CALIBRATION_MODEL_MISMATCH", "Calibration belongs to another model")
    spec = CalibrationInput.model_validate(payload["input"])
    report = payload["report"]
    # Report selection can use its evaluation results, so the entire report cutoff,
    # not merely the end of the fit window, must be known before inference.
    available = max(spec.cutoff, max(row.label_available_at for row in spec.calibration_labels))
    if not replay:
        available = max(available, registered_at)
    if body.snapshot.as_of < available:
        raise DomainError(
            "CALIBRATION_NOT_AVAILABLE", "Calibration was not available at input cutoff"
        )
    last_target = max(row.target_session for row in spec.calibration_labels)
    if body.snapshot.bars[-1].session <= registry.calendar.after(
        last_target, spec.embargo_sessions
    ):
        raise DomainError("CALIBRATION_OVERLAP", "Input overlaps the calibration label window")
    prediction = experimental_predict(
        registry.models, model_id, body, calendar=registry.calendar, now=now, replay=replay
    )
    knots = tuple((Decimal(x), Decimal(y)) for x, y in report["knots"])
    probability = calibrated_probability(knots, prediction.expected_return)
    return CalibratedExperimentalPrediction(
        **{
            **prediction.model_dump(),
            "probability_up": probability,
            "calibration_id": calibration_id,
            "calibration_available_at": available,
        }
    )
