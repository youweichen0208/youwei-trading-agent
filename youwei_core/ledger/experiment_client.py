"""Controller-side client for the Runner's experiment control plane (S08c).

The Runner's experiment surface has two planes (S08b):

- control plane (aud=runner-exec, scope experiment:admin): register the
  authorization before dispatch, terminate on parent cancel/lease loss/
  attempt change, and read the computation receipts (trusted execution
  evidence);
- tool plane (aud=runner-tools, per-operation scopes): the experiment
  instance submits computations, polls status, reads artifacts.

This module is the Controller's transport for the CONTROL plane plus the
Ed25519 signing helpers. Every call re-signs a fresh token after consulting
the attempt's lease (``expiry_provider``) — renewal is always a new
signature, never an extension (the S07m rule). The Runner holds only the
public key and re-verifies each call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Sequence

import httpx
from pydantic import ValidationError

from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentInvocationEnvelope,
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentInvocationStatus,
    experiment_invocation_digest,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    AUD_RUNTIME_EXPERIMENT,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_RUN_STATUS,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    SCOPE_EXPERIMENT_ADMIN,
    sign_research_token,
)
from youwei_core.ledger.research_client import ResearchSigningKey


class ExperimentRunnerError(Exception):
    pass


@dataclass(frozen=True)
class ExperimentBinding:
    """The fields every experiment token binds (mirrors the Core
    registration row): the experiment's identity and its parent execution
    context, the parent research turn's evidence hash, and the deployment's
    execution config version."""

    experiment_invocation_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    attempt_no: int
    case_id: uuid.UUID
    evidence_sha256: str
    exec_config_version: str


def experiment_binding_from_auth(auth: ExperimentAuthorization) -> ExperimentBinding:
    return ExperimentBinding(
        experiment_invocation_id=auth.experiment_invocation_id,
        tenant_id=auth.tenant_id,
        run_id=auth.run_id,
        job_id=auth.job_id,
        attempt_no=auth.attempt_no,
        case_id=auth.case_id,
        evidence_sha256=auth.evidence_sha256,
        exec_config_version=auth.exec_config_version,
    )


def sign_experiment_token(
    key: ResearchSigningKey,
    binding: ExperimentBinding,
    *,
    aud: str,
    scopes: tuple[str, ...] | list[str],
    exp: datetime,
) -> str:
    """Sign one experiment grant with the Controller's Ed25519 key."""
    return sign_research_token(
        key.private_key_pem,
        kid=key.kid,
        aud=aud,
        scopes=scopes,
        invocation_id=binding.experiment_invocation_id,
        tenant_id=binding.tenant_id,
        run_id=binding.run_id,
        job_id=binding.job_id,
        attempt_no=binding.attempt_no,
        case_id=binding.case_id,
        evidence_sha256=binding.evidence_sha256,
        exec_config_version=key.exec_config_version,
        exp=exp,
    )


def sign_experiment_admin_token(
    key: ResearchSigningKey,
    binding: ExperimentBinding,
    *,
    exp: datetime,
) -> str:
    """The control-plane grant (aud=runner-exec, scope experiment:admin)."""
    return sign_experiment_token(
        key, binding,
        aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,), exp=exp,
    )


def sign_experiment_dispatch_tokens(
    key: ResearchSigningKey,
    binding: ExperimentBinding,
    *,
    exp: datetime,
    tool_exp: datetime,
) -> tuple[str, str, str]:
    """Sign the three grants for one experiment instance dispatch.

    Returns (dispatch_token, runtime_token, tool_token):
    - dispatch (aud=runner-exec, scope experiment:run): authorizes the
      Controller's POST to the Runner's dispatch entry;
    - runtime (aud=runtime-experiment, scope experiment:run): carried in
      the envelope for the experiment container to verify independently;
    - tool (aud=runner-tools, submit/status/read): what the container's
      sandbox tools use on the tool plane.

    ``tool_exp`` may exceed ``exp`` by design: the tool grant must outlive
      the container turn it authorizes, and its expiry is the Runner-side
      BACKSTOP (the primary control is the Controller's terminate call);
      it is bounded by the experiment's registered duration limit.
    """
    dispatch = sign_experiment_token(
        key, binding,
        aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,), exp=exp,
    )
    runtime = sign_experiment_token(
        key, binding,
        aud=AUD_RUNTIME_EXPERIMENT, scopes=(SCOPE_EXPERIMENT_RUN,), exp=exp,
    )
    tool = sign_research_token(
        key.private_key_pem,
        kid=key.kid,
        aud=AUD_RUNNER_TOOLS,
        scopes=(
            SCOPE_EXPERIMENT_SUBMIT,
            SCOPE_EXPERIMENT_STATUS,
            SCOPE_EXPERIMENT_READ,
        ),
        invocation_id=binding.experiment_invocation_id,
        tenant_id=binding.tenant_id,
        run_id=binding.run_id,
        job_id=binding.job_id,
        attempt_no=binding.attempt_no,
        case_id=binding.case_id,
        evidence_sha256=binding.evidence_sha256,
        exec_config_version=key.exec_config_version,
        exp=tool_exp,
    )
    return dispatch, runtime, tool


class ExperimentRunnerClient:
    """HTTP transport to the Runner's experiment control plane.

    Mirrors ``research_client.ResearchRunnerClient``: every call signs a
    fresh admin token from the current lease (``expiry_provider``) and is
    bound to the experiment's registration; the Runner re-verifies the
    audience, scope, and binding on each request.
    """

    def __init__(self, base_url: str, *, transport=None, timeout: float = 30.0):
        if not base_url:
            raise ValueError("experiment runner URL required")
        self.http = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=timeout, trust_env=False
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def aclose(self):
        await self.http.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        *,
        key: ResearchSigningKey,
        binding: ExperimentBinding,
        expiry_provider: Callable[[], Awaitable[datetime]],
        json_body=None,
    ) -> httpx.Response:
        exp = await expiry_provider()
        token = sign_experiment_admin_token(key, binding, exp=exp)
        response = await self.http.request(
            method,
            path,
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code not in (200, 202):
            detail = ""
            try:
                detail = str(response.json().get("detail", ""))[:200]
            except Exception:  # noqa: BLE001 — detail is best-effort
                pass
            raise ExperimentRunnerError(
                f"experiment runner {method} {path} failed "
                f"({response.status_code}): {detail}"
            )
        return response

    async def register_authorization(
        self,
        *,
        key: ResearchSigningKey,
        auth: ExperimentAuthorization,
        binding: ExperimentBinding,
        expiry_provider: Callable[[], Awaitable[datetime]],
    ) -> None:
        """Register the experiment authorization on the Runner (D2
        requirement 1: registered BEFORE any dispatch; the Runner enforces
        the limits on every tool call from here on)."""
        await self._call(
            "POST",
            "/v1/experiment-authorizations",
            key=key,
            binding=binding,
            expiry_provider=expiry_provider,
            json_body=auth.model_dump(mode="json"),
        )

    async def terminate(
        self,
        *,
        key: ResearchSigningKey,
        binding: ExperimentBinding,
        expiry_provider: Callable[[], Awaitable[datetime]],
    ) -> None:
        """Terminate the experiment (D2 requirement 3): the Runner rejects
        new computations and cancels/cleans up running ones."""
        await self._call(
            "DELETE",
            f"/v1/experiment-authorizations/{binding.experiment_invocation_id}",
            key=key,
            binding=binding,
            expiry_provider=expiry_provider,
        )

    async def receipts(
        self,
        *,
        key: ResearchSigningKey,
        binding: ExperimentBinding,
        expiry_provider: Callable[[], Awaitable[datetime]],
    ) -> list[ExperimentComputationReceipt]:
        """Fetch the experiment's computation receipts (trusted execution
        evidence; D2 requirement 4). Every receipt must belong to this
        experiment — the Runner is trusted for what ran, but the response is
        still bound before it reaches the ledger."""
        response = await self._call(
            "GET",
            f"/v1/experiment-authorizations/{binding.experiment_invocation_id}/receipts",
            key=key,
            binding=binding,
            expiry_provider=expiry_provider,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExperimentRunnerError(
                "experiment runner receipts response is not JSON"
            ) from exc
        if not isinstance(payload, list):
            raise ExperimentRunnerError(
                "experiment runner receipts response is not a list"
            )
        try:
            receipts = [
                ExperimentComputationReceipt.model_validate(item) for item in payload
            ]
        except ValidationError as exc:
            raise ExperimentRunnerError(
                "experiment runner returned invalid receipts"
            ) from exc
        for receipt in receipts:
            if receipt.experiment_invocation_id != binding.experiment_invocation_id:
                raise ExperimentRunnerError(
                    "experiment runner returned a receipt for another experiment"
                )
        return receipts


async def run_experiment_instance(
    client: ExperimentRunnerClient,
    key: ResearchSigningKey,
    binding: ExperimentBinding,
    request: ExperimentInvocationRequest,
    *,
    expiry_provider: Callable[[], Awaitable[datetime]],
    tool_exp: datetime,
    poll_seconds: float = 0.5,
) -> ExperimentInvocationResult:
    """Dispatch one experiment instance and poll until terminal.

    Mirrors ``run_research_via_runner``: the dispatch/status tokens are
    re-signed from the current lease before EVERY call (renewal is a new
    signature); the runtime + tool grants are signed once here (their exp
    is the bounded backstop — the primary control is the Controller's
    terminate call on failure/cancel)."""
    import asyncio

    exp = await expiry_provider()
    _, runtime_token, tool_token = sign_experiment_dispatch_tokens(
        key, binding, exp=exp, tool_exp=tool_exp
    )
    view = await client.dispatch_instance(
        key=key,
        binding=binding,
        request=request,
        runtime_token=runtime_token,
        tool_token=tool_token,
        expiry_provider=expiry_provider,
    )
    digest = experiment_invocation_digest(request)
    if view.request_sha256 != digest:
        raise ExperimentRunnerError("experiment dispatch binding mismatch")
    while view.status == "running":
        await asyncio.sleep(poll_seconds)
        view = await client.instance_status(
            key=key, binding=binding, expiry_provider=expiry_provider
        )
    if view.status != "succeeded" or view.result is None:
        raise ExperimentRunnerError(
            view.error or f"experiment instance dispatch {view.status}"
        )
    return view.result

    async def dispatch_instance(
        self,
        *,
        key: ResearchSigningKey,
        binding: ExperimentBinding,
        request: ExperimentInvocationRequest,
        runtime_token: str,
        tool_token: str,
        expiry_provider: Callable[[], Awaitable[datetime]],
    ) -> ExperimentInvocationStatus:
        """Submit one experiment instance dispatch (aud=runner-exec, scope
        experiment:run). The envelope carries the runtime + tool grants for
        the container; the dispatch token (freshly signed from the current
        lease) authorizes the HTTP call itself."""
        exp = await expiry_provider()
        dispatch_token = sign_experiment_token(
            key, binding,
            aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,), exp=exp,
        )
        envelope = ExperimentInvocationEnvelope(
            request=request,
            runtime_token=runtime_token,
            tool_token=tool_token,
        )
        response = await self.http.post(
            "/v1/experiment-invocations",
            json=envelope.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {dispatch_token}"},
        )
        if response.status_code not in (200, 202):
            detail = ""
            try:
                detail = str(response.json().get("detail", ""))[:200]
            except Exception:  # noqa: BLE001 — detail is best-effort
                pass
            raise ExperimentRunnerError(
                f"experiment dispatch failed ({response.status_code}): {detail}"
            )
        try:
            return ExperimentInvocationStatus.model_validate_json(response.content)
        except ValidationError as exc:
            raise ExperimentRunnerError(
                "experiment runner returned an invalid dispatch status"
            ) from exc

    async def instance_status(
        self,
        *,
        key: ResearchSigningKey,
        binding: ExperimentBinding,
        expiry_provider: Callable[[], Awaitable[datetime]],
    ) -> ExperimentInvocationStatus:
        """Poll one experiment instance dispatch (scope experiment:run_status)."""
        exp = await expiry_provider()
        token = sign_experiment_token(
            key, binding,
            aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN_STATUS,), exp=exp,
        )
        response = await self.http.get(
            f"/v1/experiment-invocations/{binding.experiment_invocation_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            raise ExperimentRunnerError(
                f"experiment dispatch status failed ({response.status_code})"
            )
        try:
            return ExperimentInvocationStatus.model_validate_json(response.content)
        except ValidationError as exc:
            raise ExperimentRunnerError(
                "experiment runner returned an invalid dispatch status"
            ) from exc
