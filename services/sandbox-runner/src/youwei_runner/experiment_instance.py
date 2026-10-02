"""Experiment instance execution entry (S08c-2): run the fixed
agent-runtime image for one experiment turn.

This is the Runner's THIRD controlled execution path, alongside the sandbox
(execution.py) and the research container (research.py). It reuses the
research container's discipline — fixed image (digest-pinned in production),
non-root, read-only root, cap-drop ALL, no-new-privileges, resource limits,
wall-clock timeout with by-name container cleanup — and differs in exactly
one respect: egress. The experiment container may reach the approved LLM
gateway AND this Runner's experiment tool plane (owner decision D1=A; see
docs/research/s08-exploration-loop-design.md §5). It runs on a dedicated
``youwei-experiment`` network; research containers stay on their own
gateway-only network and cannot reach the Runner.

The stdin wire carries the Controller's ask (question + snapshot manifest),
the injected gateway config, the runtime-experiment grant (verified by the
container), and the runner-tools token the container's platform tools use
for sandbox_submit / sandbox_status / artifact_read. The snapshot itself is
NEVER in the wire: the Runner injects it into every computation from the
registered authorization (the instance cannot redirect its own input).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from youwei_contracts.experiment import (
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentResult,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_TOOLS,
    AUD_RUNTIME_EXPERIMENT,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    ResearchCapabilityError,
    verify_research_token,
)

from youwei_runner.settings import RunnerSettings


class ExperimentInstanceError(Exception):
    """Infrastructure-level experiment instance failure: timeout, container
    fault, or a rejected grant — distinct from an experiment result."""


@dataclass
class ExperimentRunConfig:
    """Server-side (Runner) configuration for the experiment container. The
    image, gateway, tool endpoint, and public keys all come from the
    Runner's deployment config — never from the caller's request."""

    image: str
    gateway_url: str
    gateway_host: str
    public_keys: dict[str, str]  # kid -> PEM
    exec_config_version: str
    tool_base_url: str  # how the container reaches this Runner's tool plane
    timeout_seconds: float = 300.0
    memory: str = "768m"
    cpus: str = "1.0"
    pids_limit: int = 128
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 256_000
    runtime: str = ""
    uid: int = 10001
    gid: int = 10001
    # D1=A: the experiment container reaches {gateway, Runner tool plane} on
    # a dedicated network; research containers stay on youwei-research.
    network: str = "youwei-experiment"
    gateway_credential_env: str = "YOUWEI_GATEWAY_API_KEY"


def build_experiment_run_config(settings: RunnerSettings) -> ExperimentRunConfig:
    return ExperimentRunConfig(
        image=settings.agent_runtime_image,
        gateway_url=settings.agent_runtime_gateway_url,
        gateway_host=settings.agent_runtime_gateway_host,
        public_keys=settings.agent_runtime_public_keys,
        exec_config_version=settings.agent_runtime_exec_config_version,
        tool_base_url=settings.experiment_tool_base_url,
        timeout_seconds=settings.agent_runtime_timeout_seconds,
        memory=settings.agent_runtime_memory,
        cpus=settings.agent_runtime_cpus,
        pids_limit=settings.agent_runtime_pids_limit,
        max_stdout_bytes=settings.agent_runtime_max_stdout_bytes,
        max_stderr_bytes=settings.agent_runtime_max_stderr_bytes,
        runtime=settings.runtime,
        network=settings.experiment_network,
    )


def verify_experiment_grants(
    public_keys: dict[str, str],
    runtime_token: str,
    tool_token: str,
    *,
    request: ExperimentInvocationRequest,
) -> None:
    """Verify BOTH Controller grants before any container starts.

    - the runtime grant (aud=runtime-experiment, scope experiment:run) must
      bind this exact dispatch;
    - the tool grant (aud=runner-tools, scopes experiment:submit/status/read)
      must bind the same experiment — the container's tool calls are only as
      valid as this grant, so a mismatched one fails fast here.
    """
    try:
        cap = verify_research_token(public_keys, runtime_token)
    except ResearchCapabilityError as exc:
        raise ExperimentInstanceError(
            f"experiment runtime grant rejected: {exc}"
        ) from exc
    if cap.aud != AUD_RUNTIME_EXPERIMENT:
        raise ExperimentInstanceError("experiment runtime grant has wrong audience")
    if SCOPE_EXPERIMENT_RUN not in cap.scopes:
        raise ExperimentInstanceError(
            "experiment runtime grant missing experiment:run scope"
        )
    if (
        cap.invocation_id != request.experiment_invocation_id
        or cap.tenant_id != request.tenant_id
        or cap.run_id != request.run_id
        or cap.job_id != request.job_id
        or cap.attempt_no != request.attempt_no
        or cap.case_id != request.case_id
        or cap.evidence_sha256 != request.evidence_sha256
        or cap.exec_config_version != request.exec_config_version
    ):
        raise ExperimentInstanceError("experiment runtime grant binding mismatch")

    try:
        tool = verify_research_token(public_keys, tool_token)
    except ResearchCapabilityError as exc:
        raise ExperimentInstanceError(
            f"experiment tool grant rejected: {exc}"
        ) from exc
    if tool.aud != AUD_RUNNER_TOOLS:
        raise ExperimentInstanceError("experiment tool grant has wrong audience")
    required = (SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ)
    missing = [s for s in required if s not in tool.scopes]
    if missing:
        raise ExperimentInstanceError(
            f"experiment tool grant missing scopes: {', '.join(missing)}"
        )
    if (
        tool.invocation_id != request.experiment_invocation_id
        or tool.tenant_id != request.tenant_id
        or tool.case_id != request.case_id
        or tool.evidence_sha256 != request.evidence_sha256
        or tool.exec_config_version != request.exec_config_version
    ):
        raise ExperimentInstanceError("experiment tool grant binding mismatch")


def _docker_binary() -> str:
    import shutil as _shutil

    for cand in (_shutil.which("docker"), "/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if cand and Path(cand).exists():
            return cand
    raise ExperimentInstanceError("docker binary not found; the runner requires Docker")


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


async def run_experiment_container(
    config: ExperimentRunConfig,
    *,
    request_json: str,
) -> ExperimentInvocationResult:
    """Run one experiment turn in the fixed agent-runtime image (entry
    ``experiment-once``). Mirrors run_research_container's lifecycle: byte-
    capped stdout/stderr, wall-clock timeout with by-name container cleanup
    (killing the docker client alone leaks the container — the S07n lesson).
    """
    if not config.image:
        raise ExperimentInstanceError("agent-runtime image is not configured")
    if not config.gateway_url:
        raise ExperimentInstanceError("agent-runtime gateway is not configured")
    if not config.tool_base_url:
        raise ExperimentInstanceError(
            "experiment tool endpoint is not configured"
        )

    docker = _docker_binary()
    container = f"youwei-exp-{uuid.uuid4().hex[:12]}"

    cmd = [
        docker, "run", "--rm", "-i", "--name", container,
        "--label", "youwei.runner=experiment-v1",
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
        "-e", f"{config.gateway_credential_env}={_gateway_credential()}",
        "-e", f"YOUWEI_RESEARCH_PUBLIC_KEYS={json.dumps(config.public_keys, sort_keys=True)}",
    ]
    if config.runtime:
        cmd += ["--runtime", config.runtime]
    cmd += [config.image, "experiment-once"]

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
        raise ExperimentInstanceError(f"docker run failed to start: {exc}") from exc

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
        # Killing the docker-run CLIENT does not stop the container (S07n):
        # force-remove by name on any non-clean exit.
        if force_cleanup or proc.returncode != 0:
            try:
                await _run([docker, "rm", "-f", container], timeout=30.0)
            except ExperimentInstanceError:
                pass

    if timed_out:
        raise ExperimentInstanceError(
            f"experiment container exceeded {config.timeout_seconds}s and was killed"
        )

    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")

    if len(stdout_b) > config.max_stdout_bytes:
        raise ExperimentInstanceError("experiment container stdout exceeded cap")
    if len(stderr_b) > config.max_stderr_bytes:
        stderr = stderr[: config.max_stderr_bytes] + "\n... [stderr truncated]"

    return _decode_result(stdout, stderr, proc.returncode, config)


def _gateway_credential() -> str:
    import os

    return os.environ.get("YOUWEI_GATEWAY_API_KEY", "")


def _decode_result(
    stdout: str, stderr: str, exit_code: int, config: ExperimentRunConfig
) -> ExperimentInvocationResult:
    """Decode the experiment container's stdout into a result. A non-zero
    exit or a non-ok result is a failure, never a fabricated result."""
    if exit_code != 0:
        stdout_error = None
        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict) and payload.get("ok") is False:
                stdout_error = str(payload.get("error", ""))
        except json.JSONDecodeError:
            pass
        detail = stdout_error or "non-JSON stdout"
        return ExperimentInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=(
                f"experiment container exited {exit_code}: {detail} "
                f"| stderr tail: {stderr[-2000:]}"
            ),
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ExperimentInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error="experiment container returned non-JSON stdout",
        )
    if not payload.get("ok"):
        return ExperimentInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=str(payload.get("error", "experiment container returned ok=false"))[:500],
        )
    try:
        result = ExperimentResult.model_validate(payload["result"])
    except Exception as exc:
        return ExperimentInvocationResult(
            ok=False, exit_code=exit_code,
            image_digest=_image_digest(config.image),
            error=f"experiment container returned invalid result: {exc}"[:500],
        )
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = None
    return ExperimentInvocationResult(
        ok=True, result=result, usage=usage, exit_code=exit_code,
        image_digest=_image_digest(config.image),
    )


def _image_digest(image: str) -> str:
    if "@sha256:" in image:
        return image.split("@", 1)[1]
    return image


async def execute_experiment_invocation(
    request: ExperimentInvocationRequest,
    config: ExperimentRunConfig,
    *,
    runtime_token: str,
    tool_token: str,
) -> ExperimentInvocationResult:
    """Assemble the container stdin (request + injected gateway + tool
    endpoint + grants) and run the experiment container.

    The wire carries the FULL request (its binding fields are what the
    container verifies the runtime grant against), the gateway base_url/
    api_key and tool endpoint injected by the Runner from its own config,
    and both Controller grants. The request cannot redirect egress. The
    snapshot is NOT here — computations get it from the registered
    authorization (experiment_store), never from the instance.
    """
    verify_experiment_grants(
        config.public_keys,
        runtime_token,
        tool_token,
        request=request,
    )
    wire = {
        "capability_token": runtime_token,
        "tool_token": tool_token,
        "tool_endpoint": config.tool_base_url,
        "request": request.model_dump(mode="json"),
        "gateway": {
            "base_url": config.gateway_url,
            "api_key": _gateway_credential(),
        },
    }
    request_json = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return await run_experiment_container(config, request_json=request_json)
