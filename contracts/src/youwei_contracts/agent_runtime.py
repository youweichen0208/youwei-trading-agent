"""agent-runtime-v1: Controller -> Runner -> research container wire contract.

The research link (S07m) runs one Hermes research turn per case in a fixed
agent-runtime image, reached through the Runner's controlled interface (the
Runner is the only service that holds container runtime permissions). This
file is the wire contract shared by Controller, Runner, and the research
container; it has no database or upstream SDK imports.

Unlike sandbox-v1 (which keys an execution by ``(job_id, attempt_no)``), the
research link keys an execution by ``invocation_id``: the prediction pipeline
runs one research turn per case within a single batch job, so a second case
must not collide with the first. The invocation is:

- independently retryable (same invocation + same content is idempotent),
- bound to tenant/run/job/attempt/case, the frozen evidence hash, and the
  execution config version,
- queried / cancelled / returned by ``invocation_id``.

Gateway wiring (``base_url``/``api_key``) is NEVER carried in the request: the
Runner injects the approved gateway endpoint and a restricted gateway
credential from its own deployment config, so a caller cannot redirect the
research container's network egress or steal supplier credentials.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from youwei_contracts.research import FrozenEvidence, ResearchProposal


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ResearchRuntimeConfig(WireModel):
    """The caller-authorized research parameters. Gateway endpoint and key are
    NOT here — the Runner injects those from deployment config (network egress
    and credentials stay server-side)."""

    model: str
    provider: str = "custom"
    max_iterations: int = Field(default=8, ge=1, le=64)
    run_budget_seconds: float | None = Field(default=None, ge=1.0)


class ResearchInvocationRequest(WireModel):
    """One research turn request, keyed by ``invocation_id``.

    ``evidence_sha256`` must equal the canonical hash of ``evidence``; the
    Runner re-checks it and rejects a mismatch before starting a container.
    """

    contract_version: Literal["agent-runtime-v1"] = "agent-runtime-v1"
    invocation_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    attempt_no: int = Field(gt=0)
    case_id: uuid.UUID
    evidence: FrozenEvidence
    evidence_sha256: str
    exec_config_version: str
    config: ResearchRuntimeConfig

    @model_validator(mode="after")
    def verify_evidence_hash(self):
        canonical = json.dumps(
            self.evidence.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        if self.evidence_sha256 != actual:
            raise ValueError("evidence_sha256 does not match the frozen evidence")
        return self


def invocation_digest(request: ResearchInvocationRequest) -> str:
    """Canonical digest of the request (mirrors request_digest in sandbox.py).

    Used for idempotency: same invocation + same content hashes identically, so
    a retry with different content is detectable and rejected by the Runner."""
    value = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(value.encode()).hexdigest()


class ResearchInvocationResult(WireModel):
    """The research container's output, returned through the Runner.

    ``image_digest`` records the actual image that ran (from the Runner's own
    deployment config), so the Controller can attribute the result to a fixed
    build. ``exit_code`` is the container's exit; a non-zero exit or a
    non-``ok`` result is a failure, not a proposal."""

    ok: bool
    proposal: ResearchProposal | None = None
    usage: dict | None = None
    error: str | None = None
    exit_code: int
    image_digest: str


class ResearchInvocationStatus(WireModel):
    """Runner-side status for one research invocation (submit/poll/cancel)."""

    contract_version: Literal["agent-runtime-v1"] = "agent-runtime-v1"
    invocation_id: uuid.UUID
    request_sha256: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    result: ResearchInvocationResult | None = None
    error: str | None = None


class ResearchInvocationEnvelope(WireModel):
    """The HTTP body a Controller sends to the Runner's research entry.

    ``request`` is the stable execution content (its digest drives idempotency
    and must NOT change across renewals). ``runtime_token`` is the
    ``aud=runtime-research`` Ed25519 grant the Controller signs for the
    research container; it is auth material kept SEPARATE from the stable
    content so renewing it does not change the request digest and collide with
    a same-invocation retry."""

    request: ResearchInvocationRequest
    runtime_token: str = Field(min_length=1)
