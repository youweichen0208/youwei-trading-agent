"""Offline experimental model training and hash-checked inference."""

import argparse
import json
import os
from pathlib import Path

from pydantic import AwareDatetime, StrictInt

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.labels import PriceLabel
from yta_alpha.models import Contract, MarketSnapshot
from yta_alpha.training import TrainConfig, predict, read_artifact, save_artifact, train_fold
from yta_alpha.walk_forward import SplitConfig


class TrainingInput(Contract):
    labels: tuple[PriceLabel, ...]
    snapshots: tuple[MarketSnapshot, ...]
    observations: tuple[MarketSnapshot, ...]
    split_config: SplitConfig
    config: TrainConfig
    fold_index: StrictInt
    cutoff: AwareDatetime


class InferenceInput(Contract):
    snapshots: tuple[MarketSnapshot, ...]


def read_input(path):
    with path.open("rb") as file:
        raw = file.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("Input too large")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train")
    train.add_argument("--input", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    infer = commands.add_parser("predict")
    infer.add_argument("--input", type=Path, required=True)
    infer.add_argument("--artifact", type=Path, required=True)
    infer.add_argument("--sha256", required=True)
    register = commands.add_parser("register")
    register.add_argument("--artifact", type=Path, required=True)
    register.add_argument("--sha256", required=True)
    registered = commands.add_parser("predict-registered")
    registered.add_argument("--input", type=Path, required=True)
    registered.add_argument("--model-id", required=True)
    replay = commands.add_parser("replay-training")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--artifact", type=Path, required=True)
    replay.add_argument("--sha256", required=True)
    replay.add_argument("--output", type=Path, required=True)
    evidence = commands.add_parser("register-training-evidence")
    evidence.add_argument("--input", type=Path, required=True)
    evidence.add_argument("--model-id", required=True)
    read_evidence = commands.add_parser("read-training-evidence")
    read_evidence.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        calendar = TradingCalendar()
        raw = read_input(args.input) if hasattr(args, "input") else None
        if args.command in {"register", "predict-registered"}:
            from yta_alpha.model_registry import ModelRegistry

            registry = ModelRegistry(os.environ["YTA_MODEL_DATABASE_URL"])
            if args.command == "register":
                identity = registry.register(args.artifact, args.sha256)
                result = {"model_id": identity, "status": "EXPERIMENTAL"}
            else:
                spec = InferenceInput.model_validate_json(raw)
                artifact = registry.get(args.model_id).artifact
                result = {
                    "model_id": args.model_id,
                    "predictions": predict(artifact, spec.snapshots, calendar=calendar),
                }
        elif args.command in {"register-training-evidence", "read-training-evidence"}:
            from yta_alpha.models import DataMode
            from yta_alpha.training_evidence_registry import TrainingEvidenceRegistry

            registry = TrainingEvidenceRegistry(os.environ["YTA_MODEL_DATABASE_URL"], calendar)
            mode = DataMode(os.environ.get("YTA_DATA_MODE", "REPLAY"))
            identity = (
                registry.create(args.model_id, TrainingInput.model_validate_json(raw), mode=mode)
                if args.command == "register-training-evidence"
                else args.id
            )
            payload = registry.get(identity, mode=mode)
            report = payload["report"]
            result = {
                "id": identity,
                "model_id": payload["model_id"],
                "data_mode": payload["data_mode"],
                "status": report["status"],
                "input_hash": report["input_hash"],
                "qualification": "UNVERIFIED",
                "promotion_allowed": False,
                "mismatched_fields": report["mismatched_fields"],
            }
        elif args.command == "replay-training":
            from yta_alpha.training_replay import replay_training, save_replay

            spec = TrainingInput.model_validate_json(raw)
            artifact = read_artifact(args.artifact, args.sha256)
            report = replay_training(artifact, spec, calendar=calendar)
            digest = save_replay(report, args.sha256, args.output)
            result = {
                "status": report["status"],
                "report_sha256": digest,
                "qualification": "UNVERIFIED",
                "promotion_allowed": False,
                "mismatched_fields": report["mismatched_fields"],
            }
            print(json.dumps(result))
            return 0 if report["status"] == "MATCHED" else 2
        elif args.command == "train":
            spec = TrainingInput.model_validate_json(raw)
            artifact = train_fold(
                spec.labels,
                spec.snapshots,
                spec.observations,
                split_config=spec.split_config,
                config=spec.config,
                fold_index=spec.fold_index,
                cutoff=spec.cutoff,
                calendar=calendar,
            )
            digest = save_artifact(artifact, args.output)
            result = {
                "status": "EXPERIMENTAL",
                "artifact_sha256": digest,
                "horizon_sessions": artifact.horizon_sessions,
                "training_samples": len(artifact.fold.train),
                "test_samples": len(artifact.fold.test),
            }
        else:
            spec = InferenceInput.model_validate_json(raw)
            artifact = read_artifact(args.artifact, args.sha256)
            result = {"predictions": predict(artifact, spec.snapshots, calendar=calendar)}
    except DomainError as error:
        code = (
            error.code
            if args.command
            in {"replay-training", "register-training-evidence", "read-training-evidence"}
            else "EXPERIMENT_FAILED"
        )
        print(json.dumps({"error_code": code}))
        return 1
    except Exception:
        print(json.dumps({"error_code": "EXPERIMENT_FAILED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
