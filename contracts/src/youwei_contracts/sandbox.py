"""sandbox-v1: bounded, platform-owned request/result types."""
import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def check_argv_env(argv: list[str], env: dict[str, str]) -> None:
    """Shared argument/environment rules for sandboxed execution (used by
    sandbox-v1 SandboxRequest and experiment-v1 computation requests).
    Environment customization must not replace interpreter/loader paths."""
    if len(argv) > 100 or any(len(v) > 4096 or "\0" in v for v in argv):
        raise ValueError("invalid sandbox argument")
    if len(env) > 32 or any(
        not re.fullmatch(r"SBX_[A-Z0-9_]+", k) or len(v) > 4096 or "\0" in v
        for k, v in env.items()
    ):
        raise ValueError("sandbox environment requires bounded SBX_* names")


# Artifact extensions the sandbox may produce.
ARTIFACT_EXTENSIONS = (".json", ".csv", ".txt", ".md")


class SnapshotBundle(WireModel):
    snapshot_id: uuid.UUID
    content: str = Field(max_length=6 * 1024 * 1024)
    manifest: dict

    @model_validator(mode="after")
    def verify_hash(self):
        actual = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.manifest.get("content_sha256") != actual:
            raise ValueError("snapshot content hash mismatch")
        return self


class SandboxRequest(WireModel):
    contract_version: Literal["sandbox-v1"] = "sandbox-v1"
    job_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int = Field(gt=0)
    tenant_id: uuid.UUID
    script: str = Field(min_length=1, max_length=1_000_000)
    argv: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict, max_length=32)
    snapshot: SnapshotBundle | None = None

    @model_validator(mode="after")
    def validate_arguments(self):
        check_argv_env(self.argv, self.env)
        return self


def request_digest(request: SandboxRequest) -> str:
    value = json.dumps(request.model_dump(mode="json"), sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


class Artifact(WireModel):
    path: str
    extension: Literal[".json", ".csv", ".txt", ".md"]
    size: int = Field(ge=0, le=16 * 1024 * 1024)
    sha256: str
    content: str

    @model_validator(mode="after")
    def validate_content(self):
        path = PurePosixPath(self.path)
        if (not self.path or path.is_absolute() or ".." in path.parts
                or "\\" in self.path or "\0" in self.path
                or path.as_posix() != self.path or path.suffix != self.extension):
            raise ValueError("invalid artifact path")
        data = self.content.encode("utf-8")
        if len(data) != self.size or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("artifact size/hash mismatch")
        return self


class ExecutionResult(WireModel):
    exit_code: int
    stdout: str = Field(max_length=70000)
    stderr: str = Field(max_length=70000)
    artifacts: list[Artifact] = Field(default_factory=list, max_length=50)
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_artifacts(self):
        if sum(a.size for a in self.artifacts) > 64 * 1024 * 1024:
            raise ValueError("artifact total over cap")
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("duplicate artifact path")
        return self


class ExecutionStatus(WireModel):
    contract_version: Literal["sandbox-v1"] = "sandbox-v1"
    job_id: uuid.UUID
    attempt_no: int
    request_sha256: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    result: ExecutionResult | None = None
    error: str | None = None
