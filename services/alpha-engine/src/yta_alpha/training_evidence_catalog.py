"""Stored evidence discovery; only detail reads perform native retraining."""

import re
from typing import Literal

from pydantic import AwareDatetime

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import ModelId
from yta_alpha.models import SHA256, Contract, DataMode
from yta_alpha.training_evidence_contracts import TrainingEvidenceId, TrainingEvidenceResponse
from yta_alpha.training_evidence_registry import PREFIX, training_input


class TrainingEvidenceSummary(Contract):
    id: TrainingEvidenceId
    model_id: ModelId
    data_mode: DataMode
    content_hash: SHA256
    input_hash: SHA256
    status: Literal["MATCHED", "MISMATCH"]
    registered_at: AwareDatetime
    qualification: Literal["UNVERIFIED"] = "UNVERIFIED"
    verification: Literal["stored-integrity-checked-retraining-on-detail"] = (
        "stored-integrity-checked-retraining-on-detail"
    )


class TrainingEvidencePage(Contract):
    items: tuple[TrainingEvidenceSummary, ...]
    next_cursor: TrainingEvidenceId | None


def stored_summary(row, registered, model_id, mode):
    """Validate saved bindings without claiming the inputs were retrained now."""
    try:
        payload = row["payload"]
        if (
            set(payload)
            != {
                "schema_version",
                "model_id",
                "data_mode",
                "registration_content_hash",
                "artifact_file_sha256",
                "input",
                "report",
            }
            or payload["schema_version"] != "training-evidence-v1"
        ):
            raise ValueError("Invalid stored envelope")
        normalized = training_input(payload["input"]).model_dump(mode="json")
        response = TrainingEvidenceResponse(
            id=row["id"],
            content_hash=row["content_hash"],
            model_id=payload["model_id"],
            data_mode=payload["data_mode"],
            registration_content_hash=payload["registration_content_hash"],
            artifact_file_sha256=payload["artifact_file_sha256"],
            input_hash=content_hash(normalized),
            report=payload["report"],
        )
        artifact = registered.artifact
        if (
            normalized != payload["input"]
            or content_hash(payload) != response.content_hash
            or row["model_id"] != model_id
            or response.model_id != model_id
            or row["data_mode"] != mode.value
            or response.data_mode != mode
            or response.registration_content_hash
            != content_hash(registered.model_dump(mode="json"))
            or response.artifact_file_sha256 != registered.artifact_file_sha256
            or response.report.expected_artifact_content_hash
            != content_hash(artifact.model_dump(mode="json"))
            or response.report.expected_model_sha256 != artifact.model_sha256
            or response.report.expected_source_hashes != artifact.source_hashes
        ):
            raise ValueError("Stored evidence binding differs")
        return TrainingEvidenceSummary(
            id=response.id,
            model_id=model_id,
            data_mode=mode,
            content_hash=response.content_hash,
            input_hash=response.input_hash,
            status=response.report.status,
            registered_at=row["created_at"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DomainError(
            "TRAINING_EVIDENCE_INTEGRITY_ERROR", "Stored training evidence is invalid"
        ) from error


def training_evidence_page(registry, model_id, mode, *, cursor=None, limit=20):
    mode = DataMode(mode)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if cursor is not None and not re.fullmatch(PREFIX + r"[a-f0-9]{64}", cursor):
        raise DomainError("INVALID_CURSOR", "Invalid training evidence cursor")
    registered = registry.models.get(model_id)
    if registered.artifact.data_mode != mode:
        raise DomainError("MODEL_NOT_FOUND", "Model not found in this mode", status=404)
    items = []
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT created_at,id FROM yta.training_evidence "
                "WHERE id=%s AND model_id=%s AND data_mode=%s",
                (cursor, model_id, mode.value),
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Cursor is outside the evidence scope")
        query = "SELECT * FROM yta.training_evidence WHERE model_id=%s AND data_mode=%s"
        params = [model_id, mode.value]
        if anchor:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        with connection.cursor(name="training_evidence_catalog") as stream:
            stream.itersize = 1
            stream.execute(query, params)
            for row in stream:
                if len(items) == limit:
                    return TrainingEvidencePage(items=tuple(items), next_cursor=items[-1].id)
                items.append(stored_summary(row, registered, model_id, mode))
    return TrainingEvidencePage(items=tuple(items), next_cursor=None)
