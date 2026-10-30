"""Read-only price comparisons derived from registered independent evaluation evidence."""

import argparse
import hashlib
import json
import os
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path

from yta_alpha import calibration, training
from yta_alpha import forecast as forecast_module
from yta_alpha.calibrate_cli import CalibrationInput
from yta_alpha.calibration_registry import CalibrationRegistry
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, direction, forecast, rounded, snapshot_hash
from yta_alpha.walk_forward import label_id


def price_metrics(expected, actual):
    if len(expected) != len(actual) or any(
        not isinstance(value, Decimal) or not value.is_finite() or value < -1
        for value in (*expected, *actual)
    ):
        raise DomainError("INVALID_EVALUATION_INPUT", "Expected aligned finite Decimal returns")
    if not actual:
        return {"sample_count": 0, "mae": None, "direction_accuracy": None}
    try:
        with localcontext() as context:
            context.prec = 50
            pairs = [
                (rounded(left), rounded(right))
                for left, right in zip(expected, actual, strict=True)
            ]
            count = Decimal(len(pairs))
            error = sum((abs(left - right) for left, right in pairs), Decimal(0)) / count
            correct = sum(direction(left) == direction(right) for left, right in pairs)
            return {
                "sample_count": len(pairs),
                "mae": format(rounded(error), "f"),
                "direction_accuracy": format(rounded(Decimal(correct) / count), "f"),
            }
    except DecimalException as error:
        raise DomainError("INVALID_EVALUATION_INPUT", "Returns exceed arithmetic bounds") from error


def evaluate_registered(registry: CalibrationRegistry, calibration_id: str):
    # get verifies content identity AND replays labels, split separation and calibration.
    payload = registry.get(calibration_id)
    artifact = registry.models.get(payload["model_id"]).artifact
    return evaluate_verified_evidence(payload, artifact, registry.calendar, calibration_id)


def evaluate_verified_evidence(payload, artifact, calendar, calibration_id):
    """Calculate from evidence already checked by calibration_registry.verify_record.

    Also used during restore, without opening a second credential-bearing connection.
    """
    spec = CalibrationInput.model_validate(payload["input"])
    labels = sorted(spec.evaluation_labels, key=lambda row: row.base_session)
    bases = {snapshot_hash(snapshot): snapshot for snapshot in spec.snapshots}
    snapshots = tuple(bases[row.base_snapshot_hash] for row in labels)
    predictions = training.predict(artifact, snapshots, calendar=calendar)
    expected = tuple(Decimal(row["expected_return"]) for row in predictions)
    actual = tuple(row.actual_return for row in labels)
    momentum = None
    if artifact.horizon_sessions == 5:
        momentum = tuple(
            forecast(
                snapshot, now=snapshot.as_of, calendar=calendar, git_sha="0" * 40
            ).expected_return
            for snapshot in snapshots
        )
    cohort = {
        "label_ids": [label_id(row) for row in labels],
        "cutoff": spec.cutoff.isoformat(),
        "data_mode": artifact.data_mode.value,
        "horizon_sessions": artifact.horizon_sessions,
        "calendar_version": calendar.version,
    }
    report = {
        "schema_version": "experimental-price-evaluation-v1",
        "status": "EXPERIMENTAL",
        "qualification": "UNVERIFIED",
        "scope": "POST_CALIBRATION_HISTORICAL_EVALUATION",
        "model_id": payload["model_id"],
        "model_sha256": artifact.model_sha256,
        "calibration_id": calibration_id,
        "calibration_content_hash": content_hash(payload),
        "protocol_version": "eod-price-v1",
        "return_basis": "split-only-price-return",
        "cohort": cohort,
        "cohort_hash": content_hash(cohort),
        "metrics": {
            "model": price_metrics(expected, actual),
            "zero-return-v1": price_metrics((Decimal(0),) * len(actual), actual),
            "momentum20-v1": price_metrics(momentum, actual) if momentum is not None else None,
        },
        "momentum_unavailable_reason": None if momentum is not None else "FIVE_SESSION_ONLY",
        "manifest": [
            {
                "label_id": label_id(row),
                "base_session": row.base_session.isoformat(),
                "target_session": row.target_session.isoformat(),
                "feature_as_of": row.feature_as_of.isoformat(),
                "label_available_at": row.label_available_at.isoformat(),
                "base_snapshot_hash": row.base_snapshot_hash,
                "observation_snapshot_hash": row.observation_snapshot_hash,
                "actual_return": format(row.actual_return, "f"),
                "actual_direction": row.actual_direction.value,
                "model_expected_return": format(expected[index], "f"),
                "momentum_expected_return": format(momentum[index], "f")
                if momentum is not None
                else None,
                "zero_expected_return": "0.000000000000",
            }
            for index, row in enumerate(labels)
        ],
        "code_sha256": {
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in {
                "price_evaluation": __file__,
                "forecast": forecast_module.__file__,
                "training": training.__file__,
                "calibration": calibration.__file__,
            }.items()
        },
    }
    return {**report, "content_hash": content_hash(report)}


def compare_registered(registry, calibration_ids):
    if not 2 <= len(calibration_ids) <= 10 or len(set(calibration_ids)) != len(calibration_ids):
        raise DomainError("INVALID_COMPARISON", "Select two to ten distinct calibration versions")
    reports = [evaluate_registered(registry, identity) for identity in calibration_ids]
    if len({report["cohort_hash"] for report in reports}) != 1:
        raise DomainError(
            "EVALUATION_COHORT_MISMATCH", "Comparison requires identical labels and cutoff"
        )
    return {
        "status": "EXPERIMENTAL",
        "qualification": "UNVERIFIED",
        "cohort_hash": reports[0]["cohort_hash"],
        "reports": reports,
    }


def main(argv=None):
    from yta_alpha.calendar import TradingCalendar

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-id", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        registry = CalibrationRegistry(os.environ["YTA_MODEL_DATABASE_URL"], TradingCalendar())
        result = (
            evaluate_registered(registry, args.calibration_id[0])
            if len(args.calibration_id) == 1
            else compare_registered(registry, args.calibration_id)
        )
    except Exception:
        print(json.dumps({"error_code": "MODEL_EVALUATION_FAILED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
