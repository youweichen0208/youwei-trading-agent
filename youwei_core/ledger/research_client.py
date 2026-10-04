"""Controller-side research link over the Runner's controlled interface (S07m).

The Controller no longer spawns the agent-runtime as a LOCAL subprocess; it
submits one research invocation per case to the Runner's research entry
(``POST /v1/research-invocations``), which is the only service with container
runtime permissions. The Controller signs TWO Ed25519 grants per invocation:

- ``aud=runner-exec``: authorizes the Runner to dispatch the invocation.
- ``aud=runtime-research``: carried inside the envelope for the research
  container to verify independently (the container can verify, never sign).

The envelope keeps the stable request content (digest drives idempotency)
separate from the runtime token (auth material), so renewing the token does
not change the request digest. The Controller holds the Ed25519 PRIVATE key;
the Runner and container hold only public keys.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable

import httpx
from pydantic import ValidationError

from youwei_contracts.agent_runtime import (
    ResearchInvocationEnvelope,
    ResearchInvocationRequest,
    ResearchInvocationResult,
    ResearchInvocationStatus,
    invocation_digest,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
    sign_research_token,
)


class ResearchRunnerError(Exception):
    pass


@dataclass(frozen=True)
class ResearchSigningKey:
    """Controller-side Ed25519 signing material for the research link.

    ``exec_config_version`` here is the DEPLOYMENT's execution config version;
    it must equal the ``exec_config_version`` stamped on every request so the
    Runner's binding check passes. A config change bumps both together."""

    kid: str
    private_key_pem: str
    exec_config_version: str


@dataclass(frozen=True)
class RunnerResearchConfig:
    """Controller-side wiring to reach the Runner's research entry (S07m).

    ``client`` is the HTTP transport to the Runner; ``key`` the Controller's
    Ed25519 signing material; ``research_config`` the caller-authorized model
    parameters (model/provider/iterations — the gateway endpoint/key are
    injected by the Runner, never carried here). The per-batch token expiry is
    derived inside ``run_batch_predictions`` by re-checking the current
    attempt's lease (the Controller never extends a grant past the lease).

    ``experiment_client`` + ``experiment_limits`` (both or neither) enable the
    S08 exploration loop: research turns may answer with an experiment
    request, which the orchestrator runs through the Runner's experiment
    surface before re-entering the turn."""

    client: "ResearchRunnerClient"
    key: ResearchSigningKey
    research_config: dict
    experiment_client: "object | None" = None  # ExperimentRunnerClient
    experiment_limits: "object | None" = None  # contracts ExperimentLimits


def sign_invocation_tokens(
    key: ResearchSigningKey,
    request: ResearchInvocationRequest,
    *,
    exp: datetime,
) -> tuple[str, str]:
    """Sign both grants for one invocation: (runner_exec_token, runtime_token).

    Both bind the same invocation identity (invocation/tenant/run/job/attempt/
    case/evidence/config). They differ only in audience.
    """
    common = dict(
        kid=key.kid,
        invocation_id=request.invocation_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        job_id=request.job_id,
        attempt_no=request.attempt_no,
        case_id=request.case_id,
        evidence_sha256=request.evidence_sha256,
        exec_config_version=key.exec_config_version,
        exp=exp,
    )
    runner_exec = sign_research_token(
        key.private_key_pem, aud=AUD_RUNNER_EXEC, scopes=(SCOPE_RESEARCH_RUN,), **common
    )
    runtime = sign_research_token(
        key.private_key_pem, aud=AUD_RUNTIME_RESEARCH, scopes=(SCOPE_RESEARCH_RUN,), **common
    )
    return runner_exec, runtime


class ResearchRunnerClient:
    """Controller's HTTP transport to the Runner's research entry.

    Mirrors ``sandbox.client.RunnerClient`` but keys by ``invocation_id`` and
    carries the ``ResearchInvocationEnvelope`` (stable request + runtime token).
    """

    def __init__(self, base_url: str, *, transport=None, poll_seconds: float = 0.2):
        if not base_url:
            raise ValueError("research runner URL required")
        self.http = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=30.0, trust_env=False
        )
        self.poll_seconds = poll_seconds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def aclose(self):
        await self.http.aclose()

    async def submit(
        self,
        envelope: ResearchInvocationEnvelope,
        *,
        runner_exec_token: str,
    ) -> ResearchInvocationStatus:
        path = f"/v1/research-invocations/{envelope.request.invocation_id}"
        digest = invocation_digest(envelope.request)

        async def call(method, url, body=None, token=None):
            headers = {"Authorization": f"Bearer {token or runner_exec_token}"}
            async with self.http.stream(method, url, json=body, headers=headers) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 64 * 1024 * 1024:
                        raise ResearchRunnerError("research runner response too large")
                if response.status_code not in (200, 202):
                    raise ResearchRunnerError(
                        f"research runner {method} failed ({response.status_code})"
                    )
            try:
                view = ResearchInvocationStatus.model_validate_json(data)
            except ValidationError as exc:
                raise ResearchRunnerError("invalid research runner result contract") from exc
            if view.invocation_id != envelope.request.invocation_id or view.request_sha256 != digest:
                raise ResearchRunnerError("research runner result binding mismatch")
            return view

        view = await call(
            "POST", "/v1/research-invocations", envelope.model_dump(mode="json")
        )
        return view


async def run_research_via_runner(
    client: ResearchRunnerClient,
    key: ResearchSigningKey,
    request: ResearchInvocationRequest,
    *,
    expiry_provider: Callable[[], Awaitable[datetime]],
    poll_seconds: float = 0.2,
) -> ResearchInvocationResult:
    """Submit one research invocation and poll until terminal, with renewal.

    The Controller re-checks the current attempt's lease (via
    ``expiry_provider``) before EACH token use; a renewed lease yields a NEW
    Ed25519 signature. This is the ONLY way a grant's validity is extended —
    the inner runtime token is never extended just because the outer Runner
    lease was renewed.
    """
    path = f"/v1/research-invocations/{request.invocation_id}"

    async def call_with_fresh_token(method, url, *, scopes, body=None):
        exp = await expiry_provider()
        runner_exec, _ = sign_invocation_tokens(key, request, exp=exp)
        # For status/cancel we sign a fresh token with the narrower scope.
        from youwei_contracts.research_capability import sign_research_token

        common = dict(
            kid=key.kid,
            invocation_id=request.invocation_id,
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            job_id=request.job_id,
            attempt_no=request.attempt_no,
            case_id=request.case_id,
            evidence_sha256=request.evidence_sha256,
            exec_config_version=key.exec_config_version,
            exp=exp,
        )
        token = sign_research_token(
            key.private_key_pem, aud=AUD_RUNNER_EXEC, scopes=scopes, **common
        )
        headers = {"Authorization": f"Bearer {token}"}
        async with client.http.stream(method, url, json=body, headers=headers) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 64 * 1024 * 1024:
                    raise ResearchRunnerError("research runner response too large")
            if response.status_code not in (200, 202):
                raise ResearchRunnerError(
                    f"research runner {method} failed ({response.status_code})"
                )
        view = ResearchInvocationStatus.model_validate_json(data)
        if view.invocation_id != request.invocation_id:
            raise ResearchRunnerError("research runner result binding mismatch")
        return view

    # submit
    exp = await expiry_provider()
    runner_exec, runtime = sign_invocation_tokens(key, request, exp=exp)
    envelope = ResearchInvocationEnvelope(request=request, runtime_token=runtime)
    view = await call_with_fresh_token(
        "POST", "/v1/research-invocations", scopes=(SCOPE_RESEARCH_RUN,), body=envelope.model_dump(mode="json")
    )
    while view.status == "running":
        await asyncio.sleep(poll_seconds)
        view = await call_with_fresh_token(
            "GET", path, scopes=(SCOPE_RESEARCH_STATUS,)
        )
    if view.status != "succeeded" or view.result is None:
        raise ResearchRunnerError(view.error or f"research invocation {view.status}")
    return view.result


def build_research_request(
    *,
    invocation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_no: int,
    case_id: uuid.UUID,
    evidence,
    evidence_sha256: str,
    exec_config_version: str,
    config: dict,
) -> ResearchInvocationRequest:
    """Assemble a ResearchInvocationRequest from Controller-side frozen inputs.

    ``evidence`` is the FrozenEvidence bundle; ``evidence_sha256`` its canonical
    hash (computed by the caller over the frozen content). ``config`` carries
    model/iterations only — the gateway endpoint/key are injected by the Runner.
    ``exec_config_version`` must equal the signing key's version.
    """
    from youwei_contracts.agent_runtime import ResearchRuntimeConfig

    return ResearchInvocationRequest(
        invocation_id=invocation_id,
        tenant_id=tenant_id,
        run_id=run_id,
        job_id=job_id,
        attempt_no=attempt_no,
        case_id=case_id,
        evidence=evidence,
        evidence_sha256=evidence_sha256,
        exec_config_version=exec_config_version,
        config=ResearchRuntimeConfig(**config),
    )


def evidence_sha256(evidence) -> str:
    """Canonical hash of a FrozenEvidence bundle (matches the contracts
    ``ResearchInvocationRequest.verify_evidence_hash`` canonicalization)."""
    import hashlib, json

    canonical = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def make_runner_research_fetcher(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_no: int,
    snapshot: dict,
    batch_manifest: dict,
    client: "ResearchRunnerClient",
    key: ResearchSigningKey,
    expiry_provider: Callable[[], Awaitable[datetime]],
    research_config: dict,
    attribution_sink: "Callable[[dict], None] | None" = None,
):
    """Build a ``fetch_proposal(case, bars, quant) -> ResearchProposal`` over the
    Runner's research entry (one invocation per case).

    Each case shares the batch's single frozen snapshot (S06a); only the case
    plan differs. The Controller signs a fresh grant per invocation, keyed by
    ``invocation_id`` so multiple cases in one batch job never collide.
    """
    from youwei_core.ledger.evidence import build_frozen_evidence
    from youwei_core.ledger.agent_client import AgentRuntimeError

    async def fetch_proposal(case, bars, quant):
        evidence = build_frozen_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            case=case,
            snapshot=snapshot,
            batch_manifest=batch_manifest,
            quant=quant,
        )
        request = build_research_request(
            invocation_id=uuid.uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            job_id=job_id,
            attempt_no=attempt_no,
            case_id=evidence.case.case_id,
            evidence=evidence,
            evidence_sha256=evidence_sha256(evidence),
            exec_config_version=key.exec_config_version,
            config=research_config,
        )
        result = await run_research_via_runner(
            client, key, request, expiry_provider=expiry_provider
        )
        if attribution_sink is not None:
            # D2 版本留痕 (2026-10-03): surface what ACTUALLY ran alongside the
            # configured routing — the container's attribution record (prompt
            # hash, resolved non-sensitive execution config + hash, and the
            # provider-returned model id with its observation scope), the
            # image digest of the container that produced the proposal, its
            # usage observation and the execution-config version. The
            # exploratory report records this next to the configured model
            # attribution.
            attribution_sink({
                "attribution": (
                    result.attribution.model_dump(mode="json")
                    if result.attribution is not None else None
                ),
                "image_digest": result.image_digest,
                "usage": result.usage,
                "exec_config_version": key.exec_config_version,
            })
        proposal = result.proposal
        if proposal is None:
            raise AgentRuntimeError(
                f"research invocation returned ok but no proposal: {result.error or 'unknown'}"
            )
        if proposal.run_id != run_id or proposal.case_id != evidence.case.case_id:
            raise AgentRuntimeError(
                "proposal run_id/case_id does not match the frozen evidence"
            )
        return proposal

    return fetch_proposal
