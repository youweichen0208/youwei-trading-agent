"""Create an experimental calibration report using a trusted regression artifact."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import AwareDatetime, StrictInt

from yta_alpha.calendar import TradingCalendar
from yta_alpha.calibration import calibrate_and_evaluate
from yta_alpha.labels import PriceLabel
from yta_alpha.models import Contract, MarketSnapshot
from yta_alpha.train_cli import read_input
from yta_alpha.training import read_artifact


class CalibrationInput(Contract):
    calibration_labels: tuple[PriceLabel, ...]
    evaluation_labels: tuple[PriceLabel, ...]
    snapshots: tuple[MarketSnapshot, ...]
    observations: tuple[MarketSnapshot, ...]
    cutoff: AwareDatetime
    embargo_sessions: StrictInt = 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = read_input(args.input)
        spec = CalibrationInput.model_validate_json(raw)
        artifact = read_artifact(args.artifact, args.sha256)
        report = calibrate_and_evaluate(
            artifact,
            spec.calibration_labels,
            spec.evaluation_labels,
            spec.snapshots,
            spec.observations,
            cutoff=spec.cutoff,
            calendar=TradingCalendar(),
            embargo_sessions=spec.embargo_sessions,
        )
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        report["artifact_file_sha256"] = args.sha256
        output = (json.dumps(report, indent=2) + "\n").encode()
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix=".calibration-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(output)
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, args.output)
        finally:
            os.unlink(temporary)
    except Exception:
        print(json.dumps({"error_code": "CALIBRATION_FAILED"}))
        return 1
    print(
        json.dumps(
            {
                "status": "EXPERIMENTAL",
                "report_sha256": hashlib.sha256(output).hexdigest(),
                "calibration_samples": len(spec.calibration_labels),
                "evaluation_samples": len(spec.evaluation_labels),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
