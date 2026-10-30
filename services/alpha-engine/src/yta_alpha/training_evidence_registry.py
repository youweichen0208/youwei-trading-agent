"""Immutable original training inputs, registered-model binding and full retraining."""

import re

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_registry import ModelRegistry
from yta_alpha.models import DataMode
from yta_alpha.train_cli import TrainingInput
from yta_alpha.training_replay import replay_training

PREFIX = "training-evidence-"


def training_input(value):
    return TrainingInput.model_validate(
        value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    )


def verify_training_evidence(row, expected):
    """Historical verifier hashes stay historical; all comparison semantics must replay."""
    try:
        payload = row["payload"]
        report = dict(payload["report"])
        report_hash = report.pop("content_hash")
        historical_code = report.pop("verifier_code_sha256")
        current = dict(expected["report"])
        current.pop("content_hash")
        current.pop("verifier_code_sha256")
        valid = (
            set(payload) == set(expected)
            and {k: v for k, v in payload.items() if k != "report"}
            == {k: v for k, v in expected.items() if k != "report"}
            and isinstance(historical_code, str)
            and re.fullmatch(r"[a-f0-9]{64}", historical_code)
            and content_hash(report | {"verifier_code_sha256": historical_code}) == report_hash
            and report == current
            and row["model_id"] == payload["model_id"]
            and row["data_mode"] == payload["data_mode"]
            and row["content_hash"] == content_hash(payload)
            and row["id"] == PREFIX + row["content_hash"]
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise DomainError("TRAINING_EVIDENCE_INTEGRITY_ERROR", "Training evidence cannot replay")
    return payload


class TrainingEvidenceRegistry:
    def __init__(self, url, calendar):
        self.models = ModelRegistry(url)
        self.calendar = calendar

    def _connect(self):
        return self.models._connect()

    def replay(self, model_id, spec, *, mode):
        mode = DataMode(mode)
        value = training_input(spec)
        registered = self.models.get(model_id)
        if registered.artifact.data_mode != mode:
            raise DomainError("MODEL_NOT_FOUND", "Model not found in this mode", status=404)
        report = replay_training(registered.artifact, value, calendar=self.calendar)
        if report["replayed_data_mode"] != mode.value:
            raise DomainError("TRAINING_COHORT_MISMATCH", "Training inputs must use the model mode")
        return {
            "schema_version": "training-evidence-v1",
            "model_id": model_id,
            "data_mode": mode.value,
            "registration_content_hash": content_hash(registered.model_dump(mode="json")),
            "artifact_file_sha256": registered.artifact_file_sha256,
            "input": value.model_dump(mode="json"),
            "report": report,
        }

    def create(self, model_id, spec, *, mode):
        expected = self.replay(model_id, spec, mode=mode)
        digest = content_hash(expected)
        identity = PREFIX + digest
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.training_evidence(id,model_id,data_mode,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (identity, model_id, DataMode(mode).value, digest, Jsonb(expected)),
            ).fetchone()
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'model.training_evidence_registered',%s,%s)",
                    (
                        identity,
                        Jsonb(
                            {
                                "model_id": model_id,
                                "data_mode": DataMode(mode).value,
                                "status": expected["report"]["status"],
                                "qualification": "UNVERIFIED",
                            }
                        ),
                    ),
                )
            row = connection.execute(
                "SELECT * FROM yta.training_evidence WHERE id=%s", (identity,)
            ).fetchone()
            verify_training_evidence(row, expected)
        return identity

    def get(self, identity, *, mode):
        mode = DataMode(mode)
        if not re.fullmatch(PREFIX + r"[a-f0-9]{64}", identity):
            raise DomainError(
                "TRAINING_EVIDENCE_NOT_FOUND", "Training evidence not found", status=404
            )
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT * FROM yta.training_evidence WHERE id=%s AND data_mode=%s",
                (identity, mode.value),
            ).fetchone()
        if row is None:
            raise DomainError(
                "TRAINING_EVIDENCE_NOT_FOUND", "Training evidence not found", status=404
            )
        try:
            spec = training_input(row["payload"]["input"])
        except (KeyError, TypeError, ValueError) as error:
            raise DomainError(
                "TRAINING_EVIDENCE_INTEGRITY_ERROR", "Stored input is invalid"
            ) from error
        return verify_training_evidence(row, self.replay(row["model_id"], spec, mode=mode))
