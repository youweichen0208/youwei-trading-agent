"""Hermes research runtime (S07): AIAgent invocation bridge.

This module wraps the pinned Hermes ``AIAgent`` (Python 3.14 checkout on SG)
behind a small, testable seam. It is the ONLY place that imports Hermes; the
rest of the package is pure logic (see adapter.py). Because Hermes is not a
pip dependency, importing this module requires the pinned checkout on
``sys.path`` — which only exists on the SG host (or inside the deployment
image). The import is therefore deferred so the package's pure-logic surface
still imports everywhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from youwei_agent_runtime.usage import (
    UsageReport,
    _snapshot_session_usage,
    _observe_usage,
    build_execution_attribution,
)

from youwei_contracts.research import (
    FrozenEvidence, ResearchProposal, validate_proposal_references,
)
from youwei_agent_runtime.adapter import (
    ISOLATION_KWARGS,
    append_experiment_guidance,
    build_research_brief,
)
from youwei_agent_runtime.tools import (
    RESEARCH_TOOLSET,
    ToolContext,
    reset_tool_context,
    set_tool_context,
)


class HermesNotAvailable(Exception):
    """Raised when the pinned Hermes checkout is not importable."""


@dataclass(frozen=True)
class ResearchTurn:
    """One research turn's validated output plus the observed gateway usage.

    A turn carries EITHER a proposal (the final llm_adjusted answer) OR an
    experiment_request (S08 exploration loop: the research instance asks the
    Controller to run a computation it cannot do with the available tools;
    the Controller orchestrates the experiment and re-enters the turn with
    the accepted outcome). ``usage`` is a ``UsageReport`` (never a bare dict)
    so the Controller can settle cost from a labeled, completeness-aware
    observation rather than an ambiguous token blob."""

    proposal: ResearchProposal | None
    usage: UsageReport
    attribution: object | None = None  # contracts ResearchInvocationAttribution
    experiment_request: "object | None" = None  # contracts ExperimentRequest


@dataclass(frozen=True)
class ResearchConfig:
    """Gateway + model wiring for a research run. base_url must be the
    OpenAI-compatible gateway endpoint (scheme A); api_key is the gateway key,
    never a supplier credential. Cost attribution and budgeting live in the
    Controller/budget layer, not here.

    ``max_output_tokens`` caps the model's output per API call: without an
    explicit cap the provider default can truncate a verbose model's JSON
    proposal mid-stream (observed with glm-5.3, finish_reason=length)."""

    base_url: str
    api_key: str
    model: str
    provider: str = "custom"
    max_iterations: int = 8
    run_budget_seconds: float | None = None
    max_output_tokens: int = 16384


def _import_aiagent():
    try:
        from run_agent import AIAgent
    except ImportError as exc:  # pragma: no cover - only on hosts w/o Hermes
        raise HermesNotAvailable(
            "pinned Hermes checkout not on sys.path; run on SG host"
        ) from exc
    return AIAgent


def _register_research_tools() -> None:
    """Register the platform research tools into Hermes's global tool registry.

    Hermes has no per-instance custom-tool injection; custom tools live in the
    global ``tools.registry`` under a toolset, selected per agent via
    ``enabled_toolsets`` (see docs/research/runtime-version-pinning.md). This
    function drives that registry directly (the "direct-registry path") so the
    standalone ``research-once`` subprocess does not need plugin discovery.

    Registration is idempotent at the process level: the registry is global
    and a second call must not duplicate tools. Only the SG host (or the
    deployment image) has the pinned checkout, so this is exercised there;
    the pure handler/schema surface (tools.py) is tested everywhere else.
    """
    # Deferred import: tools.registry only exists inside the Hermes checkout.
    # (tools/__init__.py is side-effect free and does NOT re-export registry;
    # the module-level singleton lives at tools.registry.registry.)
    try:
        from tools.registry import registry
    except ImportError:  # pragma: no cover - only on hosts w/o Hermes
        raise HermesNotAvailable(
            "Hermes tools.registry not on sys.path; run on SG host"
        )
    from youwei_agent_runtime.tools import RESEARCH_TOOL_DEFINITIONS

    for name, schema, handler in RESEARCH_TOOL_DEFINITIONS:
        # Match the PluginContext.register_tool contract Hermes resolves at
        # _load_tools: register(name, toolset, schema, handler, ...). The
        # registry groups by toolset and get_tool_definitions(enabled_toolsets)
        # snapshots this instance's tools.
        registry.register(
            name=name,
            toolset=RESEARCH_TOOLSET,
            schema=schema,
            handler=handler,
            is_async=False,
            description=schema["description"],
        )


def make_agent(config: ResearchConfig, *, platform: str = "research"):
    """Construct an isolated Hermes AIAgent for one research run.

    Applies ISOLATION_KWARGS (skip_memory / skip_context_files /
    skip_background_review / enabled_toolsets=[RESEARCH_TOOLSET]) so Hermes has
    ONLY the platform research tools (already registered by the process entry
    via ``_register_research_tools``), no built-in toolset, no implicit memory,
    and no session search. Returns an agent whose chat() talks to the
    configured OpenAI-compatible gateway.
    """
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
        **ISOLATION_KWARGS,
    )


def parse_proposal(
    raw: str,
    *,
    run_id,
    case_id,
    model_attribution: dict | None = None,
) -> ResearchProposal:
    """Decode Hermes's textual answer and check wire/value discipline only.

    The model is instructed (via the research brief) to return a JSON object
    matching the research-v1 proposal shape. This boundary enforces the wire
    discipline declared in the contract. Citation checking requires the
    evidence bundle and is performed by run_research before returning.

    ``model_attribution``, when provided, REPLACES any self-reported model
    field: external text is untrusted input, so attribution comes from the
    runtime's configuration (what was actually called), never from the
    model's answer.
    """
    # Tolerate markdown fences / surrounding prose in a best-effort way.
    text = raw.strip()
    if text.startswith("```"):
        # strip a leading ```json ... ``` fence
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: text.rstrip().rfind("```")]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # fall back to scanning for the first balanced {...} object
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("proposal response is not JSON: " + raw[:200])
        payload = json.loads(text[start : end + 1])

    from youwei_agent_runtime.adapter import proposal_from_payload

    if model_attribution is not None:
        payload.pop("model", None)
        payload["model"] = model_attribution
    return proposal_from_payload(run_id, case_id, payload)


def parse_experiment_request_output(raw: str):
    """Parse the model's answer as an experiment request (the exploration
    loop's turn output). Same fence tolerance as parse_proposal."""
    from youwei_contracts.experiment import ExperimentRequest

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
            raise ValueError("experiment request response is not JSON: " + raw[:200])
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("experiment request must be a JSON object")
    if "experiment_request" in payload:
        payload = payload["experiment_request"]
    return ExperimentRequest.model_validate(payload)


async def run_research(
    evidence: FrozenEvidence,
    config: ResearchConfig,
    *,
    capability_token: str | None = None,
    public_keys: dict[str, str] | None = None,
    experiments: list | None = None,
) -> ResearchTurn:
    """Run one research turn and return a validated proposal plus usage.

    This is the end-to-end seam used by the Controller: build the deterministic
    brief, run an isolated AIAgent turn against the gateway, parse the answer
    into a proposal, and observe the turn's gateway usage. Cost/budget/fencing
    are the Controller's concern; this function only produces the proposal and
    a labeled usage observation.

    ``capability_token`` / ``public_keys``, when provided, are carried in a
    ToolContext contextvar for the duration of the turn so platform tool
    handlers (tools.py) can re-verify the Controller's Ed25519 grant before
    acting. They are optional so pure-logic callers (e.g. offline tests with a
    fake agent) can run without a token; without them, any tool call will be
    rejected by ``current_tool_context``. ``public_keys`` maps kid -> PEM (the
    container verifies; it never signs).

    Usage observation: the session counters are snapshotted BEFORE and AFTER
    the turn and differenced (the before value is checked, never assumed 0).
    If the checkout lacks session counters, ``_last_turn_usage`` is used as a
    last-call fallback marked incomplete. A turn that raises (timeout / stream
    break / cancel) yields an incomplete report rather than no report, so the
    Controller can record that usage was possibly incurred but unknown.
    """
    agent = make_agent(config)
    brief = build_research_brief(evidence)
    brief = append_experiment_guidance(brief, experiments)

    if capability_token is not None and public_keys is not None:
        tool_ctx = ToolContext(
            evidence=evidence,
            capability_token=capability_token,
            public_keys=public_keys,
        )
        ctx_token = set_tool_context(tool_ctx)
    else:
        ctx_token = None

    before = _snapshot_session_usage(agent)

    # chat() is synchronous; run it in a thread so the caller can cancel.
    import asyncio

    try:
        raw = await asyncio.to_thread(agent.chat, brief)
    finally:
        if ctx_token is not None:
            reset_tool_context(ctx_token)

    after = _snapshot_session_usage(agent)
    try:
        proposal = parse_proposal(
            raw,
            run_id=evidence.run_id,
            case_id=evidence.case.case_id,
            # Attribution from configuration, never from the model's self-report.
            model_attribution={"model_version": config.model, "provider": config.provider},
        )
    except ValueError:
        # not a proposal — an experiment request (the brief documents this
        # alternative output; a malformed answer fails in the parser below)
        experiment_request = parse_experiment_request_output(raw)
        usage = _observe_usage(agent, before, after)
        return ResearchTurn(
            proposal=None, usage=usage,
            attribution=build_execution_attribution(agent, brief, config),
            experiment_request=experiment_request,
        )
    validate_proposal_references(evidence, proposal, experiments=experiments)
    usage = _observe_usage(agent, before, after)
    return ResearchTurn(
        proposal=proposal, usage=usage,
        attribution=build_execution_attribution(agent, brief, config),
    )
