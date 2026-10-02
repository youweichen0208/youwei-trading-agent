"""Experiment tool definitions (S08c-2): the experiment instance's toolset.

The experiment instance (a Hermes container dispatched by the Runner) sees
exactly three tools — ``sandbox_submit`` / ``sandbox_status`` /
``artifact_read`` — which call the Runner's experiment TOOL plane over HTTP
(aud=runner-tools, per-operation scopes). This mirrors the research
toolset's authorization model: the Controller-signed tool token is carried
in a contextvar, re-verified (audience + scope + binding) before every
call, and the Runner re-verifies it again server-side.

The journal is the honest record of what the instance actually did: every
submitted computation (its code, and the last status the instance observed)
is recorded here, and the runtime assembles the final
``ExperimentResult.computations`` from the JOURNAL — never from the model's
self-report. A computation still running at turn end is excluded (only
terminal statuses are result entries); its receipt still exists on the
Runner and will surface in the Controller's receipt verification.

Nothing here imports Hermes; the whole surface is testable in the 3.14
agent-runtime environment without the pinned checkout.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentComputationRequest,
    ExperimentComputationStatus,
    ExperimentResultComputation,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_TOOLS,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    ResearchCapabilityError,
    verify_research_token,
)

EXPERIMENT_TOOLSET = "youwei-experiment"

# Terminal statuses a result entry may carry (running is not a result).
_TERMINAL = ("succeeded", "failed", "cancelled", "timeout")

# Bounded HTTP timeouts for tool-plane calls: every endpoint answers
# immediately (the Runner runs computations asynchronously), so these only
# guard against a hung Runner.
_HTTP_TIMEOUT_SECONDS = 30.0


class ExperimentToolAuthorizationError(Exception):
    """A tool call is not authorized for the current experiment run."""


@dataclass
class ComputationJournalEntry:
    """One computation as the instance experienced it: the code it
    submitted (the ONLY source of truth for the result's code fields) and
    the last status it observed."""

    computation_id: uuid.UUID
    code: str
    code_sha256: str
    last_status: str
    artifacts: list[ArtifactManifest] = field(default_factory=list)


class ComputationJournal:
    """What the instance actually did (per-run, in-memory)."""

    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, ComputationJournalEntry] = {}

    def record_submit(self, status: ExperimentComputationStatus, code: str) -> None:
        self._entries[status.computation_id] = ComputationJournalEntry(
            computation_id=status.computation_id,
            code=code,
            code_sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            last_status=status.status,
            artifacts=list(status.artifacts),
        )

    def record_status(self, status: ExperimentComputationStatus) -> None:
        entry = self._entries.get(status.computation_id)
        if entry is not None:
            entry.last_status = status.status
            entry.artifacts = list(status.artifacts)

    def terminal_results(self) -> list[ExperimentResultComputation]:
        """Result entries for every computation whose last observed status
        is terminal, ordered by first submission."""
        results = []
        for entry in self._entries.values():
            if entry.last_status not in _TERMINAL:
                continue
            results.append(
                ExperimentResultComputation(
                    computation_id=entry.computation_id,
                    code=entry.code,
                    code_sha256=entry.code_sha256,
                    status=entry.last_status,
                    artifacts=list(entry.artifacts),
                )
            )
        return results

    def running_ids(self) -> list[uuid.UUID]:
        return [
            cid
            for cid, entry in self._entries.items()
            if entry.last_status == "running"
        ]


@dataclass
class ExperimentToolContext:
    """Per-run context the experiment tool handlers read from a contextvar.

    ``capability_token`` is the aud=runtime-experiment turn grant (verified
    once at the process boundary, re-verified here per call); ``tool_token``
    is the aud=runner-tools grant the HTTP calls carry — the Runner
    re-verifies it server-side, this side re-verifies it as defense in
    depth. ``journal`` records every computation for the final result.
    """

    capability_token: str
    tool_token: str
    public_keys: dict[str, str]
    tool_endpoint: str
    experiment_invocation_id: uuid.UUID
    journal: ComputationJournal = field(default_factory=ComputationJournal)


_experiment_tool_context: contextvars.ContextVar[
    ExperimentToolContext | None
] = contextvars.ContextVar("youwei_experiment_tool_context", default=None)


def set_experiment_tool_context(ctx: ExperimentToolContext) -> contextvars.Token:
    return _experiment_tool_context.set(ctx)


def reset_experiment_tool_context(token: contextvars.Token) -> None:
    _experiment_tool_context.reset(token)


def current_experiment_tool_context() -> ExperimentToolContext:
    ctx = _experiment_tool_context.get()
    if ctx is None:
        raise ExperimentToolAuthorizationError(
            "no experiment run context; tool called outside a run"
        )
    return ctx


def require_tool_scope(
    ctx: ExperimentToolContext, scope: str
) -> None:
    """Re-verify the carried TOOL grant (aud=runner-tools) has the required
    scope and still binds this experiment (defense in depth; the Runner
    re-verifies on every call anyway)."""
    try:
        cap = verify_research_token(ctx.public_keys, ctx.tool_token)
    except ResearchCapabilityError as exc:
        raise ExperimentToolAuthorizationError(
            f"experiment tool capability rejected: {exc}"
        ) from exc
    if cap.aud != AUD_RUNNER_TOOLS:
        raise ExperimentToolAuthorizationError(
            "experiment tool capability has wrong audience"
        )
    if scope not in cap.scopes:
        raise ExperimentToolAuthorizationError(
            f"experiment tool capability missing required scope {scope!r}"
        )
    if cap.invocation_id != ctx.experiment_invocation_id:
        raise ExperimentToolAuthorizationError(
            "experiment tool capability is bound to another experiment"
        )


# --- HTTP plumbing -----------------------------------------------------------


def _tool_call(
    ctx: ExperimentToolContext, method: str, path: str, *, json_body=None
) -> dict:
    """One bounded call to the Runner's tool plane. HTTP error statuses are
    returned to the MODEL as tool output (it can adapt — e.g. stop
    submitting at a 429); only authorization/transport failures raise."""
    try:
        with httpx.Client(
            base_url=ctx.tool_endpoint,
            headers={"Authorization": f"Bearer {ctx.tool_token}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            response = client.request(method, path, json=json_body)
    except httpx.HTTPError as exc:
        raise ExperimentToolAuthorizationError(
            f"experiment tool endpoint unreachable: {exc}"
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text[:500]}
    if response.status_code >= 400:
        return {
            "error": payload.get("detail", payload.get("error", "tool call failed")),
            "status_code": response.status_code,
        }
    return payload


# --- sandbox_submit -----------------------------------------------------------


def sandbox_submit_schema() -> dict:
    return {
        "name": "sandbox_submit",
        "description": (
            "Submit ONE sandboxed computation: your Python code runs in an "
            "isolated sandbox (no network, no secrets) against the frozen "
            "snapshot described in the brief. The snapshot is injected "
            "server-side — you cannot choose or read it directly; write "
            "code against the documented input path. Poll with "
            "sandbox_status; read produced files with artifact_read. "
            "Returns the computation's status as JSON (initially running)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source. It reads the frozen snapshot JSON from the sandbox input path and writes artifacts to the output directory.",
                },
                "expected_extensions": {
                    "type": "array",
                    "items": {"type": "string", "enum": [".json", ".csv", ".txt", ".md"]},
                    "description": "Artifact extensions your code may write.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Wall-clock budget for this computation (seconds, 1..3600).",
                },
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional argv for the computation.",
                },
                "env": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional extra environment variables (SBX_* only).",
                },
            },
            "required": ["code", "expected_extensions", "timeout_seconds"],
            "additionalProperties": False,
        },
    }


def sandbox_submit_handler(args: dict, **_: Any) -> str:
    ctx = current_experiment_tool_context()
    require_tool_scope(ctx, SCOPE_EXPERIMENT_SUBMIT)
    code = args.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("code must be a non-empty string")
    request = ExperimentComputationRequest(
        experiment_invocation_id=ctx.experiment_invocation_id,
        computation_id=uuid.uuid4(),
        code=code,
        argv=args.get("argv") or [],
        env=args.get("env") or {},
        expected_extensions=args.get("expected_extensions") or [],
        timeout_seconds=float(args.get("timeout_seconds")),
    )
    payload = _tool_call(
        ctx, "POST", "/v1/experiment-computations",
        json_body=request.model_dump(mode="json"),
    )
    if "status_code" in payload:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)
    status = ExperimentComputationStatus.model_validate(payload)
    ctx.journal.record_submit(status, code)
    return status.model_dump_json()


# --- sandbox_status -----------------------------------------------------------


def sandbox_status_schema() -> dict:
    return {
        "name": "sandbox_status",
        "description": (
            "Poll one computation's status. Terminal statuses are "
            "succeeded / failed / cancelled / timeout; failed/timeout/"
            "cancelled computations may still have readable artifacts "
            "(partial=true). Returns the status as JSON."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "computation_id": {
                    "type": "string",
                    "description": "The computation_id from sandbox_submit's response.",
                },
            },
            "required": ["computation_id"],
            "additionalProperties": False,
        },
    }


def sandbox_status_handler(args: dict, **_: Any) -> str:
    ctx = current_experiment_tool_context()
    require_tool_scope(ctx, SCOPE_EXPERIMENT_STATUS)
    computation_id = args.get("computation_id")
    try:
        computation_id = uuid.UUID(str(computation_id))
    except (TypeError, ValueError):
        raise ValueError("computation_id must be a UUID string") from None
    payload = _tool_call(
        ctx, "GET",
        f"/v1/experiment-computations/{ctx.experiment_invocation_id}/{computation_id}",
    )
    if "status_code" in payload:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)
    status = ExperimentComputationStatus.model_validate(payload)
    ctx.journal.record_status(status)
    return status.model_dump_json()


# --- artifact_read ------------------------------------------------------------


def artifact_read_schema() -> dict:
    return {
        "name": "artifact_read",
        "description": (
            "Read ONE artifact produced by a computation (bounded, "
            "content-hash verified server-side). Returns the artifact "
            "(path, content, sha256) as JSON."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "computation_id": {"type": "string"},
                "path": {"type": "string", "description": "Artifact path from the status artifacts list."},
            },
            "required": ["computation_id", "path"],
            "additionalProperties": False,
        },
    }


def artifact_read_handler(args: dict, **_: Any) -> str:
    ctx = current_experiment_tool_context()
    require_tool_scope(ctx, SCOPE_EXPERIMENT_READ)
    try:
        computation_id = uuid.UUID(str(args.get("computation_id")))
    except (TypeError, ValueError):
        raise ValueError("computation_id must be a UUID string") from None
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    payload = _tool_call(
        ctx, "GET",
        f"/v1/experiment-computations/{ctx.experiment_invocation_id}/{computation_id}"
        f"/artifacts/{path}",
    )
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# --- registration ---------------------------------------------------------------

EXPERIMENT_TOOL_DEFINITIONS: tuple[tuple[str, dict, Any], ...] = (
    ("sandbox_submit", sandbox_submit_schema(), sandbox_submit_handler),
    ("sandbox_status", sandbox_status_schema(), sandbox_status_handler),
    ("artifact_read", artifact_read_schema(), artifact_read_handler),
)


def register_experiment_tools(ctx) -> None:
    """Register the experiment tools via a Hermes PluginContext."""
    for name, schema, handler in EXPERIMENT_TOOL_DEFINITIONS:
        ctx.register_tool(
            name=name,
            toolset=EXPERIMENT_TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"],
        )
