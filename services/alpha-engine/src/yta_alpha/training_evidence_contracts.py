"""Strict public-safe projection of fully retrained private training evidence."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import ModelId
from yta_alpha.models import SHA256, Contract, DataMode
from yta_alpha.training import ModelArtifact

TrainingEvidenceId = Annotated[str, Field(pattern=r"^training-evidence-[a-f0-9]{64}$")]
ArtifactField = Literal[
    "schema_version",
    "status",
    "library_version",
    "symbol",
    "data_mode",
    "horizon_sessions",
    "training_cutoff",
    "calendar_version",
    "config",
    "dataset_hash",
    "split_config",
    "fold",
    "source_hashes",
    "model_sha256",
    "model_text",
    "test_predictions",
    "test_mae",
    "zero_return_mae",
    "probability_up",
]


class TrainingReplayReport(Contract):
    schema_version: Literal["training-replay-v1"]
    status: Literal["MATCHED", "MISMATCH"]
    qualification: Literal["UNVERIFIED"]
    promotion_allowed: StrictBool
    verification: Literal["full-retraining-exact-artifact-comparison"]
    expected_data_mode: DataMode
    replayed_data_mode: DataMode
    input_hash: SHA256
    expected_artifact_content_hash: SHA256
    replayed_artifact_content_hash: SHA256
    expected_model_sha256: SHA256
    replayed_model_sha256: SHA256
    expected_source_hashes: dict[str, SHA256]
    replayed_source_hashes: dict[str, SHA256]
    matched_fields: tuple[ArtifactField, ...]
    mismatched_fields: tuple[ArtifactField, ...]
    verifier_code_sha256: SHA256
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent(self):
        fields = set(ModelArtifact.model_fields)
        matched, mismatched = set(self.matched_fields), set(self.mismatched_fields)
        payload = self.model_dump(mode="json")
        digest = payload.pop("content_hash")
        checks = {
            "data_mode": self.expected_data_mode == self.replayed_data_mode,
            "model_sha256": self.expected_model_sha256 == self.replayed_model_sha256,
            "model_text": self.expected_model_sha256 == self.replayed_model_sha256,
            "source_hashes": self.expected_source_hashes == self.replayed_source_hashes,
        }
        if (
            self.promotion_allowed
            or list(self.matched_fields) != sorted(matched)
            or list(self.mismatched_fields) != sorted(mismatched)
            or matched & mismatched
            or matched | mismatched != fields
            or (self.status == "MATCHED") != (not mismatched)
            or (self.expected_artifact_content_hash == self.replayed_artifact_content_hash)
            != (not mismatched)
            or any(equal != (field in matched) for field, equal in checks.items())
            or content_hash(payload) != digest
        ):
            raise ValueError("Training comparison identity or field partition differs")
        return self


class TrainingEvidenceResponse(Contract):
    id: TrainingEvidenceId
    content_hash: SHA256
    model_id: ModelId
    data_mode: DataMode
    registration_content_hash: SHA256
    artifact_file_sha256: SHA256
    input_hash: SHA256
    report: TrainingReplayReport

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.id != "training-evidence-" + self.content_hash
            or self.model_id != "lightgbm-experimental-" + self.registration_content_hash
            or self.input_hash != self.report.input_hash
            or self.data_mode != self.report.expected_data_mode
            or self.data_mode != self.report.replayed_data_mode
        ):
            raise ValueError("Training evidence model, input or mode binding differs")
        return self
