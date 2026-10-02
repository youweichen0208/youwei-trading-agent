"""Experiment instance runtime (S08c-2): the youwei-experiment Hermes turn.

The experiment instance is a SEPARATE Hermes container (no shared memory
with the research instance) whose job is narrow: read the Controller's ask
(question + the frozen snapshot's MANIFEST — never the content), generate
code, drive sandboxed computations through the three platform tools, and
return bounded findings. This module holds that turn's pure logic: the
deterministic brief, the findings parser, the isolated agent construction,
and the result assembly from the computation JOURNAL (what actually ran,
not what the model claims).

The wire codec (decode -> verify the runtime-experiment grant -> run ->
encode) mirrors invoke.py for the research link.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from youwei_contracts.experiment import (
    ExperimentInvocationRequest,
    ExperimentRequest,
    ExperimentResult,
)
from youwei_contracts.research_capability import (
    AUD_RUNTIME_EXPERIMENT,
    SCOPE_EXPERIMENT_RUN,
    ResearchCapabilityError,
    verify_research_token,
)

from youwei_agent_runtime.experiment_tools import (
    EXPERIMENT_TOOLSET,
    ExperimentToolContext,
    reset_experiment_tool_context,
    set_experiment_tool_context,
)
from youwei_agent_runtime.runtime import (
    ResearchConfig,
    UsageReport,
    _observe_usage,
    _snapshot_session_usage,
)

EXPERIMENT_REQUIRED_AUDIENCE = AUD_RUNTIME_EXPERIMENT
EXPERIMENT_REQUIRED_SCOPE = SCOPE_EXPERIMENT_RUN


class ExperimentInvocationError(Exception):
    """The experiment request cannot be honored (malformed, unauthorized,
    or a turn failure)."""


@dataclass(frozen=True)
class ExperimentTurn:
    """One experiment turn's assembled result plus observed usage. The
    result's computations come from the journal (the actual tool activity);
    findings/warnings come from the model's final message."""

    result: ExperimentResult
    usage: UsageReport


# --- isolation ----------------------------------------------------------------


def experiment_isolation_kwargs() -> dict:
    """The experiment instance's isolation face: same memory/context skips
    as the research instance, but the ONLY enabled toolset is
    youwei-experiment (sandbox_submit/sandbox_status/artifact_read)."""
    from youwei_agent_runtime.adapter import ISOLATION_KWARGS

    return {**ISOLATION_KWARGS, "enabled_toolsets": [EXPERIMENT_TOOLSET]}


# --- brief ---------------------------------------------------------------------


def build_experiment_brief(
    question: ExperimentRequest, snapshot_manifest: dict
) -> str:
    """Render the Controller's ask + the frozen snapshot's manifest into a
    deterministic experiment brief.

    The instance never sees the snapshot CONTENT — only its manifest (what
    the sandbox computations will see). The brief also fixes the sandbox
    contract for generated code (input path, output directory) and the
    final output format, so the model's answer parses without negotiation.
    """
    manifest_json = json.dumps(
        snapshot_manifest, sort_keys=True, ensure_ascii=False, indent=1
    )
    lines = [
        "Experiment brief (experiment-v1)",
        "",
        "## What you are asked to compute",
        f"- question: {question.question}",
        f"- motivation: {question.motivation}",
        f"- requested_shape: {question.requested_shape}",
        "",
        "## Frozen snapshot your code will see",
        "Your code does NOT receive the data in this conversation. Every",
        "computation you submit runs in an isolated sandbox (no network, no",
        "secrets) against this frozen snapshot, injected server-side:",
        "",
        "```json",
        manifest_json,
        "```",
        "",
        "## Sandbox contract for generated code",
        "- The frozen snapshot content is a JSON file available to your code",
        "  at the input path given by the environment variable SBX_INPUT_PATH",
        "  (read it with standard file APIs; it is read-only).",
        "- Write artifacts to the current working directory; only files with",
        "  the extensions you declared in expected_extensions are collected.",
        "- stdout/stderr are captured (bounded excerpts reach the record).",
        "- Keep the code small and deterministic; no network, no wall-clock",
        "  dependence, no external state.",
        "",
        "## Tools",
        "- sandbox_submit: submit one computation (code, expected_extensions,",
        "  timeout_seconds). Returns its status JSON (computation_id).",
        "- sandbox_status: poll a computation until terminal",
        "  (succeeded/failed/cancelled/timeout; partial artifacts stay readable).",
        "- artifact_read: read one produced artifact (path, content, sha256).",
        "",
        "## Your final answer (single JSON object, no prose)",
        "```json",
        '{"findings": "<bounded text: what you computed and what it says, including the numbers and which computation/artifact backs them>",',
        '"warnings": ["<optional bounded caveats>"]',
        "```",
        "",
        "Every number in findings must come from an artifact you read or a",
        "status you observed; never invent values. Cite artifacts as",
        "experiment computations by their computation_id and path.",
    ]
    return "\n".join(lines)


# --- findings parsing -----------------------------------------------------------


def parse_experiment_findings(raw: str) -> dict:
    """Parse the model's final message into ``{"findings": str, "warnings":
    [str]}``. Tolerates markdown fences; findings must be a non-empty
    bounded string, warnings bounded strings (contract limits)."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        stripped = text.rstrip()
        if stripped.endswith("```"):
            text = stripped[: stripped.rfind("```")]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ExperimentInvocationError(
                "experiment findings are not JSON: " + raw[:200]
            )
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ExperimentInvocationError(
                "experiment findings are not JSON"
            ) from exc
    if not isinstance(payload, dict):
        raise ExperimentInvocationError("experiment findings must be a JSON object")
    findings = payload.get("findings")
    if not isinstance(findings, str) or not findings.strip():
        raise ExperimentInvocationError("experiment findings must be a non-empty string")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        raise ExperimentInvocationError("experiment warnings must be a list of strings")
    return {"findings": findings, "warnings": warnings}


# --- the turn --------------------------------------------------------------------


def make_experiment_agent(config: ResearchConfig, *, platform: str = "experiment"):
    """Construct an isolated Hermes AIAgent for one experiment run (ONLY the
    youwei-experiment toolset enabled; no built-in toolsets, no memory)."""
    from youwei_agent_runtime.runtime import _import_aiagent

    AIAgent = _import_aiagent()
    return AIAgent(
        provider=config.provider,
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        platform=platform,
        max_iterations=config.max_iterations,
        run_budget_seconds=config.run_budget_seconds,
        max_tokens=config.max_output_tokens,
        **experiment_isolation_kwargs(),
    )


async def run_experiment(
    question: ExperimentRequest,
    snapshot_manifest: dict,
    config: ResearchConfig,
    *,
    capability_token: str,
    tool_token: str,
    tool_endpoint: str,
    public_keys: dict[str, str],
    experiment_invocation_id: uuid.UUID | None = None,
) -> ExperimentTurn:
    """Run one experiment turn and assemble the result from the journal.

    The turn's authorization (runtime-experiment grant) is verified at the
    process boundary (honor_experiment_request); here it is carried in the
    tool context for per-call re-verification. The final result mixes:
    - findings/warnings: the model's parsed final message,
    - computations: the JOURNAL's terminal entries (code + hashes + the
      last observed status and artifacts) — never model self-report.
    """
    agent = make_experiment_agent(config)
    brief = build_experiment_brief(question, snapshot_manifest)

    invocation_id = experiment_invocation_id or uuid.uuid4()
    tool_ctx = ExperimentToolContext(
        capability_token=capability_token,
        tool_token=tool_token,
        public_keys=public_keys,
        tool_endpoint=tool_endpoint,
        experiment_invocation_id=invocation_id,
    )
    ctx_token = set_experiment_tool_context(tool_ctx)

    before = _snapshot_session_usage(agent)
    import asyncio

    try:
        raw = await asyncio.to_thread(agent.chat, brief)
    finally:
        reset_experiment_tool_context(ctx_token)
    after = _snapshot_session_usage(agent)

    findings = parse_experiment_findings(raw)
    result = ExperimentResult(
        findings=findings["findings"],
        warnings=findings["warnings"],
        computations=tool_ctx.journal.terminal_results(),
    )
    usage = _observe_usage(agent, before, after)
    return ExperimentTurn(result=result, usage=usage)


# --- wire codec (mirrors invoke.py for the research link) -------------------------


def check_experiment_capability(
    public_keys: dict[str, str], token: str, request: ExperimentInvocationRequest
) -> None:
    """Verify the Controller's runtime-experiment grant against the request:
    audience, scope, and full binding (experiment/tenant/run/job/attempt/
    case/evidence/config)."""
    try:
        cap = verify_research_token(public_keys, token)
    except ResearchCapabilityError as exc:
        raise ExperimentInvocationError(
            f"experiment capability rejected: {exc}"
        ) from exc
    if cap.aud != EXPERIMENT_REQUIRED_AUDIENCE:
        raise ExperimentInvocationError(
            f"experiment capability has wrong audience "
            f"(expected {EXPERIMENT_REQUIRED_AUDIENCE!r})"
        )
    if EXPERIMENT_REQUIRED_SCOPE not in cap.scopes:
        raise ExperimentInvocationError(
            f"experiment capability missing required scope "
            f"{EXPERIMENT_REQUIRED_SCOPE!r}"
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
        raise ExperimentInvocationError(
            "experiment capability does not match the request's binding"
        )


def encode_experiment_result(turn: ExperimentTurn) -> str:
    usage = turn.usage.to_dict() if hasattr(turn.usage, "to_dict") else dict(turn.usage or {})
    return json.dumps(
        {
            "ok": True,
            "result": turn.result.model_dump(mode="json"),
            "usage": usage,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def encode_experiment_error(exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


async def honor_experiment_request(
    payload: dict,
    *,
    public_keys: dict[str, str],
    run_experiment_fn,  # run_experiment (injected to keep this pure-logic)
    config_factory,  # runtime.ResearchConfig
) -> str:
    """Handle one decoded experiment request: verify the grant, assemble the
    turn inputs, run, and encode the result.

    The wire (Runner-injected): capability_token (runtime-experiment grant),
    tool_token (runner-tools grant), tool_endpoint, request (the full
    ExperimentInvocationRequest — its binding fields are what the grant is
    verified against), gateway (base_url/api_key, Runner-injected)."""
    if not isinstance(payload, dict):
        raise ExperimentInvocationError("request must be a JSON object")
    token = payload.get("capability_token")
    tool_token = payload.get("tool_token")
    tool_endpoint = payload.get("tool_endpoint")
    if not isinstance(token, str) or not token:
        raise ExperimentInvocationError("missing capability_token")
    if not isinstance(tool_token, str) or not tool_token:
        raise ExperimentInvocationError("missing tool_token")
    if not isinstance(tool_endpoint, str) or not tool_endpoint:
        raise ExperimentInvocationError("missing tool_endpoint")
    try:
        request = ExperimentInvocationRequest.model_validate(payload.get("request"))
    except Exception as exc:
        raise ExperimentInvocationError(f"invalid experiment request: {exc}") from exc
    check_experiment_capability(public_keys, token, request)

    gateway = payload.get("gateway") or {}
    config = config_factory(
        base_url=gateway.get("base_url", ""),
        api_key=gateway.get("api_key", ""),
        model=request.config.model,
        provider=request.config.provider,
        max_iterations=request.config.max_iterations,
        run_budget_seconds=request.config.run_budget_seconds,
    )
    turn = await run_experiment_fn(
        request.question,
        request.snapshot_manifest,
        config,
        capability_token=token,
        tool_token=tool_token,
        tool_endpoint=tool_endpoint,
        public_keys=public_keys,
        experiment_invocation_id=request.experiment_invocation_id,
    )
    return encode_experiment_result(turn)
