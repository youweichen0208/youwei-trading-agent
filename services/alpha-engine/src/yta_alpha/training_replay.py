"""Retrain from supplied evidence and compare every artifact field without qualification."""

import hashlib
import os
import tempfile
from pathlib import Path

from yta_alpha.forecast import content_hash
from yta_alpha.train_cli import TrainingInput
from yta_alpha.training import ModelArtifact, train_fold


def replay_training(artifact, spec, *, calendar):
    # Revalidate Python values before serialization; model_copy is not validation.
    original = ModelArtifact.model_validate(artifact.model_dump(mode="python"))
    inputs = TrainingInput.model_validate(spec.model_dump(mode="python"))
    reproduced = train_fold(
        inputs.labels,
        inputs.snapshots,
        inputs.observations,
        split_config=inputs.split_config,
        config=inputs.config,
        fold_index=inputs.fold_index,
        cutoff=inputs.cutoff,
        calendar=calendar,
    )
    expected, actual = original.model_dump(mode="json"), reproduced.model_dump(mode="json")
    fields = sorted(ModelArtifact.model_fields)
    matched = [name for name in fields if expected[name] == actual[name]]
    mismatched = [name for name in fields if expected[name] != actual[name]]
    report = {
        "schema_version": "training-replay-v1",
        "status": "MATCHED" if not mismatched else "MISMATCH",
        "qualification": "UNVERIFIED",
        "promotion_allowed": False,
        "verification": "full-retraining-exact-artifact-comparison",
        "expected_data_mode": original.data_mode.value,
        "replayed_data_mode": reproduced.data_mode.value,
        "input_hash": content_hash(inputs.model_dump(mode="json")),
        "expected_artifact_content_hash": content_hash(expected),
        "replayed_artifact_content_hash": content_hash(actual),
        "expected_model_sha256": original.model_sha256,
        "replayed_model_sha256": reproduced.model_sha256,
        "expected_source_hashes": original.source_hashes,
        "replayed_source_hashes": reproduced.source_hashes,
        "matched_fields": matched,
        "mismatched_fields": mismatched,
        "verifier_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return report | {"content_hash": content_hash(report)}


def save_replay(report, artifact_sha256, path):
    """Bind a report to the trusted artifact file; atomically publish without overwrite."""
    payload = {
        "schema_version": "training-replay-file-v1",
        "artifact_file_sha256": artifact_sha256,
        "report": report,
    }
    payload["content_hash"] = content_hash(payload)
    import json

    raw = (json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".training-replay-")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    return hashlib.sha256(raw).hexdigest()
