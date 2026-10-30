"""Append-only historical experimental price reports with semantic replay on reads."""

import argparse
import json
import os
import re

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from yta_alpha.calibration_registry import CalibrationRegistry
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation import evaluate_registered
from yta_alpha.model_evaluation_contracts import EvaluationComparisonRequest
from yta_alpha.models import DataMode

PREFIX = "model-evaluation-experimental-"


def verify_evaluation(identity, digest, payload, replayed):
    report = dict(payload)
    embedded = report.pop("content_hash", None)
    if digest != embedded or content_hash(report) != digest or identity != PREFIX + digest:
        raise DomainError("MODEL_EVALUATION_INTEGRITY_ERROR", "Report identity differs")
    hashes = report.pop("code_sha256", None)
    current = dict(replayed)
    current.pop("content_hash")
    current_hashes = current.pop("code_sha256")
    if (
        not isinstance(hashes, dict)
        or hashes.keys() != current_hashes.keys()
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in hashes.values()
        )
        or report != current
    ):
        raise DomainError("MODEL_EVALUATION_INTEGRITY_ERROR", "Report cannot be reproduced")
    # Historical source hashes remain historical; require identical semantics.
    return payload


class ModelEvaluationRegistry:
    def __init__(self, url, calendar):
        self.calibrations = CalibrationRegistry(url, calendar)

    def create(self, calibration_id):
        report = evaluate_registered(self.calibrations, calibration_id)
        digest = report["content_hash"]
        identity = PREFIX + digest
        with self.calibrations.models._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_evaluation_records"
                "(id,calibration_id,model_version_id,content_hash,data_mode,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (
                    identity,
                    calibration_id,
                    report["model_id"],
                    digest,
                    report["cohort"]["data_mode"],
                    Jsonb(report),
                ),
            ).fetchone()
            saved = connection.execute(
                "SELECT calibration_id,model_version_id,content_hash,data_mode,payload "
                "FROM yta.model_evaluation_records WHERE id=%s",
                (identity,),
            ).fetchone()
            if saved != {
                "calibration_id": calibration_id,
                "model_version_id": report["model_id"],
                "content_hash": digest,
                "data_mode": report["cohort"]["data_mode"],
                "payload": report,
            }:
                raise DomainError("MODEL_EVALUATION_CONFLICT", "Stored evaluation conflicts")
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'model.price_evaluation_registered',%s,%s)",
                    (
                        identity,
                        Jsonb(
                            {
                                "model_id": report["model_id"],
                                "calibration_id": calibration_id,
                                "content_hash": digest,
                                "qualification": "UNVERIFIED",
                            }
                        ),
                    ),
                )
        return identity

    def compare(self, identities, *, data_mode: DataMode | None = None):
        try:
            request = EvaluationComparisonRequest(evaluation_ids=identities)
        except ValidationError as error:
            raise DomainError("INVALID_COMPARISON", "Select two to ten distinct reports") from error
        # Read explicit immutable identities, never substitute newly generated reports.
        reports = [self.get(identity, data_mode=data_mode) for identity in request.evaluation_ids]
        first = reports[0]
        if any(
            report["cohort"] != first["cohort"]
            or report["cohort_hash"] != first["cohort_hash"]
            or report["protocol_version"] != first["protocol_version"]
            or report["return_basis"] != first["return_basis"]
            for report in reports[1:]
        ):
            raise DomainError(
                "EVALUATION_COHORT_MISMATCH", "Comparison requires identical labels and cutoff"
            )
        return {
            "schema_version": "experimental-report-comparison-v1",
            "status": "EXPERIMENTAL",
            "qualification": "UNVERIFIED",
            "evaluation_ids": list(request.evaluation_ids),
            "cohort_hash": first["cohort_hash"],
            "reports": reports,
        }

    def assess_quality(self, identity, policy, *, reference_id=None):
        from yta_alpha.model_quality import HistoricalQualityPolicy, assess_historical_quality

        policy = HistoricalQualityPolicy.model_validate(policy)
        candidate = self.get(identity, data_mode=policy.data_mode)
        reference = (
            self.get(reference_id, data_mode=policy.data_mode) if reference_id is not None else None
        )
        return assess_historical_quality(candidate, policy, reference)

    def get(self, identity, *, data_mode: DataMode | None = None):
        if not re.fullmatch(PREFIX + r"[a-f0-9]{64}", identity):
            raise DomainError("MODEL_EVALUATION_NOT_FOUND", "Evaluation not found", status=404)
        with self.calibrations.models._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT calibration_id,model_version_id,content_hash,data_mode,payload "
                "FROM yta.model_evaluation_records WHERE id=%s "
                "AND (%s::text IS NULL OR data_mode=%s)",
                (
                    identity,
                    data_mode.value if data_mode is not None else None,
                    data_mode.value if data_mode is not None else None,
                ),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_EVALUATION_NOT_FOUND", "Evaluation not found", status=404)
        report = row["payload"]
        if (
            row["calibration_id"] != report["calibration_id"]
            or row["model_version_id"] != report["model_id"]
            or row["data_mode"] != report["cohort"]["data_mode"]
        ):
            raise DomainError("MODEL_EVALUATION_INTEGRITY_ERROR", "Report scope differs")
        replayed = evaluate_registered(self.calibrations, row["calibration_id"])
        return verify_evaluation(identity, row["content_hash"], report, replayed)


def main(argv=None):
    from yta_alpha.calendar import TradingCalendar

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create").add_argument("--calibration-id", required=True)
    sub.add_parser("get").add_argument("--id", required=True)
    sub.add_parser("compare").add_argument("--id", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        registry = ModelEvaluationRegistry(os.environ["YTA_MODEL_DATABASE_URL"], TradingCalendar())
        result = (
            {"id": registry.create(args.calibration_id), "qualification": "UNVERIFIED"}
            if args.command == "create"
            else registry.compare(args.id)
            if args.command == "compare"
            else registry.get(args.id)
        )
    except Exception:
        print(json.dumps({"error_code": "MODEL_EVALUATION_REGISTRY_FAILED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
