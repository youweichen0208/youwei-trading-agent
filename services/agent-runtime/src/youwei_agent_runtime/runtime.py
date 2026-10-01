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


# The session-scoped token counters Hermes accumulates across a turn (verified
# against the pinned checkout 7fa45eb, agent/turn_usage.py + agent/agent_init.py).
# ``prompt_tokens``/``completion_tokens``/``total_tokens`` are the legacy/canonical
# keys; ``input_tokens``/``output_tokens``/``cache_*``/``reasoning_tokens`` are the
# canonical breakdown. These fields OVERLAP (prompt = input + cache_read +
# cache_write) and must never be summed for billing — they are carried verbatim
# for the Controller's cost map to price each category separately.
_SESSION_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

# The session counter attribute name backing each token field on the agent.
_SESSION_COUNTER_ATTRS = {
    "prompt_tokens": "session_prompt_tokens",
    "completion_tokens": "session_completion_tokens",
    "total_tokens": "session_total_tokens",
    "input_tokens": "session_input_tokens",
    "output_tokens": "session_output_tokens",
    "cache_read_tokens": "session_cache_read_tokens",
    "cache_write_tokens": "session_cache_write_tokens",
    "reasoning_tokens": "session_reasoning_tokens",
}


@dataclass(frozen=True)
class UsageReport:
    """Gateway usage observed for one research turn.

    This is an OBSERVATION of Hermes's session counters / last-call usage, not
    an invoice. ``source`` records which Hermes signal produced it;
    ``complete`` is true only when the covered scope is verified AND no usage
    was lost this turn (timeout / stream break / cancel / unclear coverage all
    force ``complete=False`` with reasons). Token counts are nullable non-
    negative integers: unknown is ``None``, confirmed-zero is ``0``. Fields
    overlap (prompt/input, completion/output, cache/reasoning are subsets) and
    must be priced by the Controller's versioned cost map per category, never
    summed. Final reconciliation always defers to the Gateway/provider record.
    """

    source: str  # "session_delta" | "last_call_fallback" | "unavailable"
    scope: str  # "chat_turn" | "last_api_call" | "unknown"
    complete: bool
    incomplete_reasons: tuple[str, ...] = ()
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    api_calls: int | None = None

    def to_dict(self) -> dict:
        """JSON-safe wire shape (the fields the Controller decodes)."""
        return {
            "source": self.source,
            "scope": self.scope,
            "complete": self.complete,
            "incomplete_reasons": list(self.incomplete_reasons),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "api_calls": self.api_calls,
        }


@dataclass(frozen=True)
class _SessionUsageSnapshot:
    """A point-in-time read of Hermes's session token counters (all nullable:
    None when an older checkout lacks that counter)."""

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    api_calls: int | None


def _read_counter(agent, attr: str) -> int | None:
    """Read one session counter as a non-negative int, or None if the checkout
    does not expose it (or the test double has no counters)."""
    value = getattr(agent, attr, None)
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _snapshot_session_usage(agent) -> _SessionUsageSnapshot | None:
    """Snapshot the session counters; returns None when the agent exposes none
    of them (a fake without counters, or a checkout predating session accounting)."""
    values = {}
    for field in _SESSION_TOKEN_FIELDS:
        values[field] = _read_counter(agent, _SESSION_COUNTER_ATTRS[field])
    values["api_calls"] = _read_counter(agent, "session_api_calls")
    if all(v is None for v in values.values()):
        return None
    return _SessionUsageSnapshot(**values)


def _sub(a: int | None, b: int | None) -> int | None:
    """Difference of two counter readings; None if either side is unknown."""
    if a is None or b is None:
        return None
    return max(a - b, 0)


def _delta_usage(before: _SessionUsageSnapshot, after: _SessionUsageSnapshot) -> UsageReport:
    """Build a complete turn-level report from the before/after snapshots.
    ``complete=True`` only when every counter is present on both sides and the
    delta is non-negative (no counter rolled backwards)."""
    reasons: list[str] = []
    fields: dict[str, int | None] = {}
    for field in _SESSION_TOKEN_FIELDS:
        a = getattr(after, field)
        b = getattr(before, field)
        d = _sub(a, b)
        fields[field] = d
        if d is None:
            reasons.append(f"missing_{field}_counter")
    api_before = before.api_calls
    api_after = after.api_calls
    fields["api_calls"] = _sub(api_after, api_before)
    if fields["api_calls"] is None:
        reasons.append("missing_api_calls_counter")
    # A backwards counter means the session was reset mid-turn (or the checkout
    # re-keyed); the delta is then unreliable.
    for field in _SESSION_TOKEN_FIELDS:
        a = getattr(after, field)
        b = getattr(before, field)
        if a is not None and b is not None and a < b:
            reasons.append(f"{field}_counter_reset")
    complete = not reasons
    return UsageReport(
        source="session_delta",
        scope="chat_turn",
        complete=complete,
        incomplete_reasons=tuple(reasons),
        **fields,
    )


def _from_last_call(agent) -> UsageReport:
    """Compatibility fallback: Hermes's ``_last_turn_usage`` dict (the LAST
    API call's canonical usage, not the turn total). Always marked incomplete
    because it does not cover retries / tool-loop / auxiliary calls."""
    last = getattr(agent, "_last_turn_usage", None)
    if not last:
        return UsageReport(
            source="unavailable",
            scope="unknown",
            complete=False,
            incomplete_reasons=("no_usage_signal",),
        )
    def _g(*names):
        for name in names:
            v = last.get(name) if isinstance(last, dict) else getattr(last, name, None)
            if v is not None:
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if n >= 0:
                    return n
        return None
    return UsageReport(
        source="last_call_fallback",
        scope="last_api_call",
        complete=False,
        incomplete_reasons=("last_call_scope_only",),
        prompt_tokens=_g("prompt_tokens", "input_tokens"),
        completion_tokens=_g("completion_tokens", "output_tokens"),
        total_tokens=_g("total_tokens"),
        input_tokens=_g("input_tokens"),
        output_tokens=_g("output_tokens"),
        cache_read_tokens=_g("cache_read_tokens", "cache_read_input_tokens"),
        cache_write_tokens=_g("cache_write_tokens", "cache_creation_input_tokens"),
        reasoning_tokens=_g("reasoning_tokens"),
        api_calls=None,
    )


@dataclass(frozen=True)
class ResearchTurn:
    """One research turn's validated proposal plus the observed gateway usage.
    ``usage`` is a ``UsageReport`` (never a bare dict) so the Controller can
    settle cost from a labeled, completeness-aware observation rather than an
    ambiguous token blob."""

    proposal: ResearchProposal
    usage: UsageReport


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
    public_keys: dict[str, str] | None = None,
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
    proposal = parse_proposal(raw, run_id=evidence.run_id, case_id=evidence.case.case_id)
    validate_proposal_references(evidence, proposal)
    usage = _observe_usage(agent, before, after)
    return ResearchTurn(proposal=proposal, usage=usage)


def _observe_usage(agent, before: _SessionUsageSnapshot | None, after: _SessionUsageSnapshot | None) -> UsageReport:
    """Choose the usage report for a completed turn: session delta when both
    snapshots exist, else the last-call fallback, else unavailable."""
    if before is not None and after is not None:
        return _delta_usage(before, after)
    return _from_last_call(agent)
