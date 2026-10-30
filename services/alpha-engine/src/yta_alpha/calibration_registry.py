"""Append-only calibration records, including evidence needed to reproduce the report."""

import re
from pathlib import Path

from psycopg.types.json import Jsonb

from yta_alpha.calibrate_cli import CalibrationInput
from yta_alpha.calibration import calibrate_and_evaluate
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_registry import ModelRegistry

PREFIX = "calibration-experimental-"


def rebuild_report(artifact, spec, calendar):
    return calibrate_and_evaluate(
        artifact,
        spec.calibration_labels,
        spec.evaluation_labels,
        spec.snapshots,
        spec.observations,
        cutoff=spec.cutoff,
        calendar=calendar,
        embargo_sessions=spec.embargo_sessions,
    )


def verify_record(identity, digest, payload, artifact, calendar):
    if (
        set(payload) != {"schema_version", "model_id", "input", "report"}
        or payload["schema_version"] != "calibration-registration-v1"
        or content_hash(payload) != digest
        or identity != PREFIX + digest
    ):
        raise DomainError("CALIBRATION_INTEGRITY_ERROR", "Registered record identity differs")
    spec = CalibrationInput.model_validate(payload["input"])
    replayed = rebuild_report(artifact, spec, calendar)
    saved = dict(payload["report"])
    # Old source hashes remain evidence. Replay requires identical semantics, not the
    # current file's hash pretending to be the historical implementation's identity.
    old_code = saved.pop("code_sha256", None)
    replayed.pop("code_sha256")
    if (
        not isinstance(old_code, str)
        or not re.fullmatch(r"[a-f0-9]{64}", old_code)
        or saved != replayed
    ):
        raise DomainError("CALIBRATION_INTEGRITY_ERROR", "Registered report cannot be reproduced")
    return payload


class CalibrationRegistry:
    def __init__(self, url, calendar):
        self.models = ModelRegistry(url)
        self.calendar = calendar

    def create(self, model_id, spec: CalibrationInput):
        spec = CalibrationInput.model_validate(spec.model_dump(mode="json"))
        artifact = self.models.get(model_id).artifact
        report = rebuild_report(artifact, spec, self.calendar)
        payload = {
            "schema_version": "calibration-registration-v1",
            "model_id": model_id,
            "input": spec.model_dump(mode="json"),
            "report": report,
        }
        digest = content_hash(payload)
        identity = PREFIX + digest
        with self.models._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.calibration_records"
                "(id,model_version_id,content_hash,data_mode,payload) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (identity, model_id, digest, report["data_mode"], Jsonb(payload)),
            ).fetchone()
            saved = connection.execute(
                "SELECT model_version_id,content_hash,data_mode,payload "
                "FROM yta.calibration_records WHERE id=%s",
                (identity,),
            ).fetchone()
            if (
                saved is None
                or saved["model_version_id"] != model_id
                or saved["content_hash"] != digest
                or saved["data_mode"] != report["data_mode"]
                or saved["payload"] != payload
            ):
                raise DomainError("CALIBRATION_VERSION_CONFLICT", "Stored record differs")
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'model.calibration_registered',%s,%s)",
                    (
                        identity,
                        Jsonb(
                            {
                                "model_id": model_id,
                                "content_hash": digest,
                                "status": "EXPERIMENTAL",
                                "qualification": "UNVERIFIED",
                            }
                        ),
                    ),
                )
        return identity

    def assess_quality(self, identity, policy):
        from yta_alpha.calibration_reports import calibration_report
        from yta_alpha.probability_quality import (
            ProbabilityQualityPolicy,
            assess_probability_quality,
        )

        policy = ProbabilityQualityPolicy.model_validate(
            policy.model_dump(mode="json")
            if isinstance(policy, ProbabilityQualityPolicy)
            else policy
        )
        report = calibration_report(self, identity, policy.data_mode)
        return assess_probability_quality(report.model_dump(mode="json"), policy)

    def get(self, identity):
        return self.get_with_timestamp(identity)[0]

    def get_with_timestamp(self, identity):
        if not re.fullmatch(PREFIX + r"[a-f0-9]{64}", identity):
            raise DomainError("CALIBRATION_NOT_FOUND", "Calibration record not found", status=404)
        with self.models._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT model_version_id,content_hash,data_mode,payload,created_at "
                "FROM yta.calibration_records WHERE id=%s",
                (identity,),
            ).fetchone()
        if row is None:
            raise DomainError("CALIBRATION_NOT_FOUND", "Calibration record not found", status=404)
        if (
            row["model_version_id"] != row["payload"]["model_id"]
            or row["data_mode"] != row["payload"]["report"]["data_mode"]
        ):
            raise DomainError("CALIBRATION_INTEGRITY_ERROR", "Record scope differs")
        artifact = self.models.get(row["model_version_id"]).artifact
        return (
            verify_record(identity, row["content_hash"], row["payload"], artifact, self.calendar),
            row["created_at"],
        )


def main(argv=None):
    import argparse
    import json
    import os

    from yta_alpha.calendar import TradingCalendar
    from yta_alpha.train_cli import read_input

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--model-id", required=True)
    create.add_argument("--input", type=Path, required=True)
    get = commands.add_parser("get")
    get.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        registry = CalibrationRegistry(os.environ["YTA_MODEL_DATABASE_URL"], TradingCalendar())
        if args.command == "create":
            spec = CalibrationInput.model_validate_json(read_input(args.input))
            identity = registry.create(args.model_id, spec)
            result = {"id": identity, "status": "EXPERIMENTAL", "qualification": "UNVERIFIED"}
        else:
            # Avoid dumping raw evidence/model input to operational logs by default.
            payload = registry.get(args.id)
            result = {"id": args.id, "model_id": payload["model_id"], "report": payload["report"]}
    except Exception:
        print(json.dumps({"error_code": "CALIBRATION_REGISTRY_FAILED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
