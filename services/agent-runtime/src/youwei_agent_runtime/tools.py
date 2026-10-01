"""Research tool definitions (S07i): the platform's Hermes tool surface.

Hermes has no per-instance custom-tool injection; platform tools are
registered into the global ``tools.registry`` under the ``youwei-research``
toolset (see docs/research/runtime-version-pinning.md "平台工具注册机制核实")
and selected via ``enabled_toolsets=["youwei-research"]``.

This module holds the pure, Hermes-free part of that surface: the toolset
name, the JSON schemas, the handlers (which read the frozen evidence via a
contextvar), and the capability-scope check. Nothing here imports Hermes, so
the whole surface is testable in the 3.14 agent-runtime environment without
the pinned checkout; the registration step (``runtime._register_research_tools``)
is the only part that touches ``tools.registry`` at run time.

Authorization model (architecture §10): the Controller signs a per-job
capability token; the agent-runtime verifies it once at the process boundary
(``invoke._check_capability``) and carries it in a contextvar so each tool
handler can re-check the required scope before acting. A tool never grants
itself authority from its arguments alone.
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass
from typing import Any

from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import SCOPE_RESEARCH_RUN

RESEARCH_TOOLSET = "youwei-research"

# The research-grant scope a tool call must carry. It is the same scope the
# Controller signs for the runtime-research audience (research:run); platform
# tools run inside that grant and may not exceed it.
TOOL_REQUIRED_SCOPE = SCOPE_RESEARCH_RUN


class ToolAuthorizationError(Exception):
    """A tool call is not authorized for the current run."""


@dataclass(frozen=True)
class ToolContext:
    """Per-run context a tool handler reads from a contextvar.

    ``evidence`` is the frozen bundle the Controller handed to this run (the
    ONLY market input a tool may read); ``capability_token`` is the verified
    Controller-signed Ed25519 grant; ``public_keys`` (kid -> PEM) re-verifies
    it on demand (the container can verify, never sign).
    """

    evidence: FrozenEvidence
    capability_token: str
    public_keys: dict[str, str]


_tool_context: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar(
    "youwei_tool_context", default=None
)


def set_tool_context(ctx: ToolContext) -> contextvars.Token:
    return _tool_context.set(ctx)


def reset_tool_context(token: contextvars.Token) -> None:
    _tool_context.reset(token)


def current_tool_context() -> ToolContext:
    ctx = _tool_context.get()
    if ctx is None:
        raise ToolAuthorizationError("no research run context; tool called outside a run")
    return ctx


def require_scope(ctx: ToolContext, scope: str = TOOL_REQUIRED_SCOPE) -> None:
    """Re-verify the carried grant has the required scope (defense in depth).

    The grant was already verified at the process boundary; this re-checks it
    is unexpired and still carries the scope. The container holds only public
    keys, so this re-verification cannot mint a new grant."""
    from youwei_contracts.research_capability import (
        AUD_RUNTIME_RESEARCH,
        ResearchCapabilityError,
        verify_research_token,
    )

    try:
        cap = verify_research_token(ctx.public_keys, ctx.capability_token)
    except ResearchCapabilityError as exc:
        raise ToolAuthorizationError(f"research capability rejected: {exc}") from exc
    if cap.aud != AUD_RUNTIME_RESEARCH:
        raise ToolAuthorizationError("research capability has wrong audience")
    if scope not in cap.scopes:
        raise ToolAuthorizationError(f"research capability missing required scope {scope!r}")


# --- snapshot_manifest ------------------------------------------------------


def snapshot_manifest_schema() -> dict:
    """JSON schema for the snapshot_manifest tool (no arguments — the tool
    reports the run's frozen evidence manifest)."""
    return {
        "name": "snapshot_manifest",
        "description": (
            "Report the frozen evidence snapshot manifest for the current "
            "research run: snapshot id, kind, as_of, mode, content hash, row "
            "count, coverage per security, and referenced source versions. "
            "Use this to understand exactly what point-in-time data the run "
            "is allowed to see before reasoning about it."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }


def snapshot_manifest_handler(args: dict, **_: Any) -> str:
    """Return the frozen evidence manifest as a compact JSON string.

    This is the ONLY data a research run may reason from (architecture §3.1):
    the content-addressed, PIT-frozen bundle the Controller handed over. No
    live data, no database, no wall-clock time.
    """
    ctx = current_tool_context()
    require_scope(ctx)
    evidence = ctx.evidence
    evidence.evidence.verify_content_hash()
    ev = evidence.evidence
    manifest = ev.manifest
    summary = {
        "snapshot_id": str(ev.snapshot_id),
        "kind": ev.kind,
        "as_of": ev.as_of,
        "mode": ev.mode,
        "content_sha256": ev.content_sha256,
        "row_count": manifest.get("row_count", len(ev.content)),
        "schema": manifest.get("schema"),
        "code_version": manifest.get("code_version"),
        "coverage": manifest.get("coverage"),
        "sources": manifest.get("sources"),
    }
    return json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- registration -----------------------------------------------------------

# The tool definitions this package registers under RESEARCH_TOOLSET. Each is
# (name, schema, handler). Handlers are sync (is_async=False) and return str,
# matching the Hermes tool contract (see the a2a platform plugin for the
# canonical shape).
RESEARCH_TOOL_DEFINITIONS: tuple[tuple[str, dict, Any], ...] = (
    ("snapshot_manifest", snapshot_manifest_schema(), snapshot_manifest_handler),
)


def register_research_tools(ctx) -> None:
    """Register the platform research tools via a Hermes PluginContext.

    ``ctx`` has ``ctx.register_tool(name, toolset, schema, handler, ...)``
    (hermes_cli.plugins.PluginContext). Used when the agent-runtime runs as a
    Hermes plugin; the direct-registry path (runtime._register_research_tools)
    is used when it drives ``tools.registry`` directly."""
    for name, schema, handler in RESEARCH_TOOL_DEFINITIONS:
        ctx.register_tool(
            name=name,
            toolset=RESEARCH_TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"],
        )
