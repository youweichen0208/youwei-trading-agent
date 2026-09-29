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

from youwei_contracts.research import (
    FrozenEvidence, ResearchProposal, validate_proposal_references,
)
from youwei_agent_runtime.adapter import ISOLATION_KWARGS, build_research_brief
from youwei_agent_runtime.tools import (
    RESEARCH_TOOLSET,
    ToolContext,
    reset_tool_context,
    set_tool_context,
)


class HermesNotAvailable(Exception):
    """Raised when the pinned Hermes checkout is not importable."""


@dataclass(frozen=True)
class ResearchConfig:
    """Gateway + model wiring for a research run. base_url must be the
    OpenAI-compatible gateway endpoint (scheme A); api_key is the gateway key,
    never a supplier credential. Cost attribution and budgeting live in the
    Controller/budget layer, not here."""

    base_url: str
    api_key: str
    model: str
    provider: str = "custom"
    max_iterations: int = 8
    run_budget_seconds: float | None = None


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
        **ISOLATION_KWARGS,
    )


def parse_proposal(raw: str, *, run_id, case_id) -> ResearchProposal:
    """Decode Hermes's textual answer and check wire/value discipline only.

    The model is instructed (via the research brief) to return a JSON object
    matching the research-v1 proposal shape. This boundary enforces the wire
    discipline declared in the contract. Citation checking requires the
    evidence bundle and is performed by run_research before returning.
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

    return proposal_from_payload(run_id, case_id, payload)


async def run_research(
    evidence: FrozenEvidence,
    config: ResearchConfig,
    *,
    capability_token: str | None = None,
    capability_secret: str | None = None,
) -> ResearchProposal:
    """Run one research turn and return a validated ResearchProposal.

    This is the end-to-end seam used by the Controller (later slice): build
    the deterministic brief, run an isolated AIAgent turn against the gateway,
    and parse the answer into a proposal. Cost/budget/fencing are the
    Controller's concern; this function only produces the proposal value.

    ``capability_token`` / ``capability_secret``, when provided, are carried
    in a ToolContext contextvar for the duration of the turn so platform tool
    handlers (tools.py) can re-verify the Controller's grant before acting.
    They are optional so pure-logic callers (e.g. offline tests with a fake
    agent) can run without a token; without them, any tool call will be
    rejected by ``current_tool_context``.
    """
    agent = make_agent(config)
    brief = build_research_brief(evidence)

    if capability_token is not None and capability_secret is not None:
        tool_ctx = ToolContext(
            evidence=evidence,
            capability_token=capability_token,
            capability_secret=capability_secret,
        )
        ctx_token = set_tool_context(tool_ctx)
    else:
        ctx_token = None

    # chat() is synchronous; run it in a thread so the caller can cancel.
    import asyncio

    try:
        raw = await asyncio.to_thread(agent.chat, brief)
    finally:
        if ctx_token is not None:
            reset_tool_context(ctx_token)
    proposal = parse_proposal(raw, run_id=evidence.run_id, case_id=evidence.case.case_id)
    validate_proposal_references(evidence, proposal)
    return proposal
