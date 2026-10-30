"""Only project training evidence after current native retraining and integrity checks."""

from yta_alpha.forecast import content_hash
from yta_alpha.training_evidence_contracts import TrainingEvidenceResponse


def training_evidence_response(registry, identity, *, mode):
    payload = registry.get(identity, mode=mode)
    return TrainingEvidenceResponse(
        id=identity,
        content_hash=content_hash(payload),
        model_id=payload["model_id"],
        data_mode=payload["data_mode"],
        registration_content_hash=payload["registration_content_hash"],
        artifact_file_sha256=payload["artifact_file_sha256"],
        input_hash=content_hash(payload["input"]),
        report=payload["report"],
    )
