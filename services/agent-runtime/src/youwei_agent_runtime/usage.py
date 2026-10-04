"""Hermes usage observations and non-sensitive execution attribution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from youwei_agent_runtime.runtime import ResearchConfig


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


def _observe_usage(agent, before: _SessionUsageSnapshot | None, after: _SessionUsageSnapshot | None) -> UsageReport:
    """Choose the usage report for a completed turn: session delta when both
    snapshots exist, else the last-call fallback, else unavailable."""
    if before is not None and after is not None:
        return _delta_usage(before, after)
    return _from_last_call(agent)


def build_execution_attribution(agent, brief: str, config: "ResearchConfig") -> "object":
    """Assemble the per-report version-traceability record (owner D2,
    2026-10-03): the deterministic brief (prompt) hash, the resolved
    NON-SENSITIVE execution config plus its canonical hash, and the
    provider-returned model id with an explicit observation scope.

    ``model_returned`` comes from the youwei local Hermes patch
    (``agent._last_turn_model``) and is the identifier of the LAST completed
    provider response — an observation of a gateway/provider return value,
    NOT proof of the underlying model identity, and deliberately NOT tied to
    the usage report's completeness (usage may be turn-cumulative while this
    value is per-response). None on unpatched checkouts or provider omission
    — never fabricated. The gateway credential never enters the config.
    """
    from youwei_contracts.agent_runtime import ResearchInvocationAttribution
    import hashlib
    import json as _json

    execution_config = {
        "model": config.model,
        "provider": config.provider,
        "max_iterations": config.max_iterations,
        "run_budget_seconds": config.run_budget_seconds,
        "max_output_tokens": config.max_output_tokens,
        "gateway_base_url": config.base_url,
    }
    config_sha = hashlib.sha256(
        _json.dumps(execution_config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return ResearchInvocationAttribution(
        brief_sha256=hashlib.sha256(brief.encode("utf-8")).hexdigest(),
        execution_config=execution_config,
        execution_config_sha256=config_sha,
        model_returned=getattr(agent, "_last_turn_model", None),
        model_returned_scope="last_completed_provider_response",
    )

