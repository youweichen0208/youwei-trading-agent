"""Research execution entry (S07m): run the fixed agent-runtime image for one
research turn, forwarding stdin/stdout, with egress limited to the approved
gateway.

This is the Runner's SECOND controlled execution path, alongside the sandbox
(execution.py). The isolation contract differs in exactly one respect from the
sandbox: the research container needs OUTBOUND access to the approved LLM
gateway (it must call the OpenAI-compatible gateway), so it is NOT
``--network none``. Everything else is identical in spirit and enforcement:

- fixed image from runner config (digest-pinned in production) — the caller
  cannot choose the image, command, mounts, network mode, or host paths
- non-root user, read-only root filesystem, cap-drop ALL, no-new-privileges,
  CPU/memory/PID limits, no secrets beyond the restricted gateway credential
- egress limited to the approved gateway ONLY (network + firewall/proxy
  enforced in the deployment; see S07m-3). The caller cannot override
  base_url/api_key — the Runner injects them from its own config.
- wall-clock timeout kills and removes the container; stdout/stderr are each
  byte-capped, and a result over the cap fails the invocation rather than
  returning a truncated JSON blob.

stdin/stdout carry the single-line JSON wire contract; stderr carries bounded,
redacted logs. The container runs with ``docker run -i`` (not detached) so its
stdin/stdout are the research turn's request/result directly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from youwei_contracts.agent_runtime import (
    ResearchInvocationRequest,
    ResearchInvocationResult,
)
from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    ResearchCapabilityError,
    verify_research_token,
)

from youwei_runner.settings import RunnerSettings


class ResearchExecutionError(Exception):
    """Infrastructure-level research failure: timeout, egress violation,
    container fault, or a rejected grant — distinct from a proposal value."""


@dataclass
class ResearchRunConfig:
    """Server-side (Runner) configuration for the research container. The
    image, gateway, and public keys all come from the Runner's deployment
    config — never from the caller's request."""

    image: str
    gateway_url: str
    gateway_host: str
    public_keys: dict[str, str]  # kid -> PEM
    exec_config_version: str
    timeout_seconds: float = 300.0
    memory: str = "768m"
    cpus: str = "1.0"
    pids_limit: int = 128
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 256_000
    runtime: str = ""
    uid: int = 10001
    gid: int = 10001
    # network attachment for the research container (deployment-managed). The
    # sandbox uses "none"; research uses a dedicated egress-limited network.
    network: str = "youwei-research"
    # the env var name holding the restricted gateway credential (injected by
    # the Runner from its own config, never from the request).
    gateway_credential_env: str = "YOUWEI_GATEWAY_API_KEY"


def build_research_config(settings: RunnerSettings) -> ResearchRunConfig:
    """Derive the research run config from Runner settings (server-side)."""
    return ResearchRunConfig(
        image=settings.agent_runtime_image,
        gateway_url=settings.agent_runtime_gateway_url,
        gateway_host=settings.agent_runtime_gateway_host,
        public_keys=settings.agent_runtime_public_keys,
        exec_config_version=settings.agent_runtime_exec_config_version,
        timeout_seconds=settings.agent_runtime_timeout_seconds,
        memory=settings.agent_runtime_memory,
        cpus=settings.agent_runtime_cpus,
        pids_limit=settings.agent_runtime_pids_limit,
        max_stdout_bytes=settings.agent_runtime_max_stdout_bytes,
        max_stderr_bytes=settings.agent_runtime_max_stderr_bytes,
        runtime=settings.runtime,
    )


def _docker_binary() -> str:
    import shutil as _shutil

    for cand in (_shutil.which("docker"), "/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if cand and Path(cand).exists():
            return cand
    raise ResearchExecutionError("docker binary not found; the runner requires Docker")


async def _run(cmd: list[str], *, timeout: float | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


async def run_research_container(
    config: ResearchRunConfig,
    *,
    request_json: str,
) -> ResearchInvocationResult:
    """Run one research turn in the fixed agent-runtime image.

    ``request_json`` is the single-line JSON the research container reads on
    stdin (the request + injected gateway + the Controller's runtime-research
    grant, already assembled by ``execute_research_request``).

    Returns the container's stdout decoded as a ResearchInvocationResult;
    raises ResearchExecutionError on infrastructure failure. A non-zero exit
    or a non-ok result is reflected in the result, not silently turned into a
    proposal.
    """
    if not config.image:
        raise ResearchExecutionError("agent-runtime image is not configured")
    if not config.gateway_url:
        raise ResearchExecutionError("agent-runtime gateway is not configured")

    docker = _docker_binary()
    container = f"youwei-res-{uuid.uuid4().hex[:12]}"

    cmd = [
        docker, "run", "--rm", "-i", "--name", container,
        "--label", "youwei.runner=research-v1",
        "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=2",
        "--network", config.network,
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", f"{config.uid}:{config.gid}",
        "--memory", config.memory,
        "--memory-swap", config.memory,
        "--cpus", config.cpus,
        "--pids-limit", str(config.pids_limit),
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size=16m,uid={config.uid},gid={config.gid},mode=0770",
        # The gateway credential is injected by the Runner from its own config
        # (a restricted, gateway-scoped key), never taken from the request.
        "-e", f"{config.gateway_credential_env}={_gateway_credential()}",
        # The research container verifies the Controller's Ed25519 grant with
        # the trusted PUBLIC key map (kid -> PEM); it can verify, never sign.
        "-e", f"YOUWEI_RESEARCH_PUBLIC_KEYS={json.dumps(config.public_keys, sort_keys=True)}",
    ]
    if config.runtime:
        cmd += ["--runtime", config.runtime]
    # The research container talks to the gateway over the approved endpoint;
    # the request carries model/iterations, the Runner injects base_url/api_key.
    cmd += [config.image, "research-once"]

    started = asyncio.get_event_loop().time()
    timed_out = False
    force_cleanup = False
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        raise ResearchExecutionError(f"docker run failed to start: {exc}") from exc

    try:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(request_json.encode("utf-8")),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            force_cleanup = True
            stdout_b, stderr_b = b"", b""
            proc.kill()
            await proc.wait()
        except asyncio.CancelledError:
            force_cleanup = True
            proc.kill()
            await proc.wait()
            raise
    finally:
        # Killing the docker-run CLIENT does not stop the container: `docker
        # run -i` detaches and the container keeps running (observed in the
        # S07 real-gateway verification: a timed-out turn kept calling the
        # gateway after the Runner had already reported the timeout).
        # Force-remove the container BY NAME unless the client exited
        # cleanly (clean exit 0 means the container already self-removed via
        # --rm). On failure paths the rm is a harmless no-op when the
        # container is already gone (the error is caught below).
        if force_cleanup or proc.returncode != 0:
            try:
                await _run([docker, "rm", "-f", container], timeout=30.0)
            except ResearchExecutionError:
                pass

    if timed_out:
        raise ResearchExecutionError(
            f"research container exceeded {config.timeout_seconds}s and was killed"
        )

    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")

    if len(stdout_b) > config.max_stdout_bytes:
        # A result over the cap is a failure, never a truncated JSON.
        raise ResearchExecutionError("research container stdout exceeded cap")
    if len(stderr_b) > config.max_stderr_bytes:
        stderr = stderr[: config.max_stderr_bytes] + "\n... [stderr truncated]"

    exit_code = proc.returncode
    return _decode_result(stdout, stderr, exit_code, config)


def _gateway_credential() -> str:
    """Resolve the restricted gateway credential from the Runner's env. This
    is a gateway-scoped key the Runner holds; it never appears in the request
    or in logs."""
    import os

    return os.environ.get("YOUWEI_GATEWAY_API_KEY", "")


def _decode_result(
    stdout: str, stderr: str, exit_code: int, config: ResearchRunConfig
) -> ResearchInvocationResult:
    """Decode the research container's stdout into a result. A non-zero exit
    or a non-ok result is a failure; a result over the stdout cap already
    raised before reaching here."""
    if exit_code != 0:
        # The container writes a structured {"ok": false, "error": ...} to
        # stdout (main.py encode_error) even on non-zero exits; prefer it over
        # the stderr tail, which is dominated by the Hermes startup banner.
        stdout_error = None
        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict) and payload.get("ok") is False:
                stdout_error = str(payload.get("error", ""))
        except json.JSONDecodeError:
            pass
        detail = stdout_error or "non-JSON stdout"
        return ResearchInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=(
                f"research container exited {exit_code}: {detail} "
                f"| stderr tail: {stderr[-2000:]}"
            ),
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ResearchInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error="research container returned non-JSON stdout",
        )
    if not payload.get("ok"):
        return ResearchInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=str(payload.get("error", "research container returned ok=false"))[:500],
        )
    from youwei_contracts.research import ResearchProposal

    try:
        proposal = ResearchProposal.model_validate(payload["proposal"])
    except KeyError:
        proposal = None
    except Exception as exc:
        return ResearchInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=f"research container returned invalid proposal: {exc}"[:500],
        )
    experiment_request = None
    if proposal is None:
        from youwei_contracts.experiment import ExperimentRequest

        raw_request = payload.get("experiment_request")
        if raw_request is None:
            return ResearchInvocationResult(
                ok=False, exit_code=exit_code,
                image_digest=_image_digest(config.image),
                error="research container returned neither proposal nor experiment_request",
            )
        try:
            experiment_request = ExperimentRequest.model_validate(raw_request)
        except Exception as exc:
            return ResearchInvocationResult(
                ok=False, exit_code=exit_code,
                image_digest=_image_digest(config.image),
                error=f"research container returned invalid experiment_request: {exc}"[:500],
            )
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = None
    return ResearchInvocationResult(
        ok=True, proposal=proposal, experiment_request=experiment_request,
        usage=usage, exit_code=exit_code,
        image_digest=_image_digest(config.image),
    )


def _image_digest(image: str) -> str:
    """Extract the digest from an image reference like `name@sha256:...`; the
    Runner records the ACTUAL image that ran for attribution."""
    if "@sha256:" in image:
        return image.split("@", 1)[1]
    return image


def verify_research_grant(
    public_keys: dict[str, str],
    token: str,
    *,
    invocation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    evidence_sha256: str,
    exec_config_version: str,
) -> None:
    """Verify the Controller's Ed25519 research grant against the invocation.

    The token's aud must be runtime-research (the Runner -> container grant),
    and its binding must match the invocation being dispatched. Called by the
    app layer before starting a container; a mismatch raises before any
    container is started.
    """
    cap = verify_research_token(public_keys, token)
    if cap.aud != AUD_RUNTIME_RESEARCH:
        raise ResearchCapabilityError("research grant has wrong audience")
    if SCOPE_RESEARCH_RUN not in cap.scopes:
        raise ResearchCapabilityError("research grant missing research:run scope")
    if (
        cap.invocation_id != invocation_id
        or cap.tenant_id != tenant_id
        or cap.case_id != case_id
        or cap.evidence_sha256 != evidence_sha256
        or cap.exec_config_version != exec_config_version
    ):
        raise ResearchCapabilityError("research grant binding mismatch")


async def execute_research_request(
    request: ResearchInvocationRequest,
    config: ResearchRunConfig,
    *,
    capability_token: str,
) -> ResearchInvocationResult:
    """Assemble the container stdin (request + injected gateway) and run it.

    The gateway base_url/api_key are injected by the Runner (server-side), so
    the caller's request cannot redirect egress or steal credentials. The
    request config carries only model/iterations/budget.
    """
    verify_research_grant(
        config.public_keys,
        capability_token,
        invocation_id=request.invocation_id,
        tenant_id=request.tenant_id,
        case_id=request.case_id,
        evidence_sha256=request.evidence_sha256,
        exec_config_version=request.exec_config_version,
    )
    # The wire the container reads: the research request plus the injected
    # gateway endpoint/key. The container's invoke.py verifies the inner
    # runtime-research grant from this same token.
    wire = {
        "capability_token": capability_token,
        "evidence": request.evidence.model_dump(mode="json"),
        "config": {
            "base_url": config.gateway_url,
            "api_key": _gateway_credential(),
            "model": request.config.model,
            "provider": request.config.provider,
            "max_iterations": request.config.max_iterations,
            "run_budget_seconds": request.config.run_budget_seconds,
        },
    }
    request_json = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return await run_research_container(config, request_json=request_json)
