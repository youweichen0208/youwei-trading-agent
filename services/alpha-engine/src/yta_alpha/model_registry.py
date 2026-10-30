"""Append-only experimental model registration. Registration never grants promotion."""

import hashlib
import re
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import SHA256, Contract
from yta_alpha.training import ModelArtifact, read_artifact

PREFIX = "lightgbm-experimental-"


class Registration(Contract):
    schema_version: Literal["trained-registration-v1"] = "trained-registration-v1"
    artifact_file_sha256: SHA256
    artifact: ModelArtifact


def verify_registration(identity, digest, payload):
    """Portable structural/hash verification; does not load the native training library."""
    value = Registration.model_validate(payload)
    if content_hash(payload) != digest or identity != PREFIX + digest:
        raise DomainError("MODEL_INTEGRITY_ERROR", "Registered content identity differs")
    artifact = value.artifact
    if hashlib.sha256(artifact.model_text.encode()).hexdigest() != artifact.model_sha256:
        raise DomainError("MODEL_INTEGRITY_ERROR", "Registered model text differs")
    return value


class ModelRegistry:
    def __init__(self, url):
        self.url = url

    def _connect(self):
        return psycopg.connect(
            self.url,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c lock_timeout=5000 -c statement_timeout=15000",
        )

    def register(self, path: Path, expected_sha256: str) -> str:
        artifact = read_artifact(path, expected_sha256)
        payload = Registration(artifact_file_sha256=expected_sha256, artifact=artifact).model_dump(
            mode="json"
        )
        digest = content_hash(payload)
        identity = PREFIX + digest
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_versions(id,model_kind,sha256,config) "
                "VALUES(%s,'TRAINED',%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (identity, digest, Jsonb(payload)),
            ).fetchone()
            saved = connection.execute(
                "SELECT model_kind,sha256,config FROM yta.model_versions WHERE id=%s",
                (identity,),
            ).fetchone()
            if (
                saved is None
                or saved["model_kind"] != "TRAINED"
                or saved["config"] != payload
                or saved["sha256"] != digest
            ):
                raise DomainError("MODEL_VERSION_CONFLICT", "Registered version conflicts")
            verify_registration(identity, saved["sha256"], saved["config"])
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'model.experimental_registered',%s,%s)",
                    (
                        identity,
                        Jsonb(
                            {
                                "registration_sha256": digest,
                                "artifact_file_sha256": expected_sha256,
                                "model_sha256": artifact.model_sha256,
                                "data_mode": artifact.data_mode.value,
                                "horizon_sessions": artifact.horizon_sessions,
                                "status": "EXPERIMENTAL",
                                "entrypoint": "private_model_registry",
                            }
                        ),
                    ),
                )
        return identity

    def get(self, identity: str) -> Registration:
        return self.get_with_timestamp(identity)[0]

    def get_with_timestamp(self, identity: str):
        if not re.fullmatch(PREFIX + r"[a-f0-9]{64}", identity):
            raise DomainError("MODEL_NOT_FOUND", "Experimental model is not registered", status=404)
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT model_kind,sha256,config,created_at FROM yta.model_versions WHERE id=%s",
                (identity,),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_NOT_FOUND", "Experimental model is not registered", status=404)
        if row["model_kind"] != "TRAINED":
            raise DomainError("MODEL_INTEGRITY_ERROR", "Registered model kind differs")
        return verify_registration(identity, row["sha256"], row["config"]), row["created_at"]
