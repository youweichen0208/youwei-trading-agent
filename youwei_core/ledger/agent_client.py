"""Controller-side agent-runtime subprocess client (S07h).

The Controller (Core worker) calls the agent-runtime — a separate Python 3.14
process with no database or supplier credentials — for one ``llm_adjusted``
research turn. The boundary is a short-lived subprocess: one JSON request on
stdin, one JSON result on stdout. This module is the Controller's half.

It stays split so the codec is pure (testable without spawning a process) and
the subprocess execution is a thin async seam. Budget accounting for the
actual gateway cost is a later slice (the gateway is reached by Hermes inside
the agent-runtime process, not through Core's GatewayClient); this slice only
gets a validated proposal back across the boundary for sealing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

from youwei_contracts.research import FrozenEvidence, ResearchProposal


class AgentRuntimeError(Exception):
    """The agent-runtime could not produce a proposal (spawn failure,
    non-zero exit, malformed result, or a rejected capability)."""


@dataclass(frozen=True)
class ResearchInvocation:
    """Everything the Controller sends across the boundary for one case."""

    capability_token: str
    evidence: FrozenEvidence
    config: dict  # runtime.ResearchConfig kwargs (base_url/api_key/model/...)


@dataclass(frozen=True)
class AgentResearchResult:
    """One agent-runtime research turn as received by the Controller.

    ``proposal`` is the validated ResearchProposal; ``usage`` is the raw usage
    report dict (source/scope/complete + token counters) decoded from the
    wire. The Controller's budget layer settles cost from ``usage``; when the
    runtime is older (proposal-only wire) ``usage`` is a synthetic unavailable
    report, never a fabricated zero."""

    proposal: ResearchProposal
    usage: dict


def encode_request(invocation: ResearchInvocation) -> str:
    """Serialize one research request line for the agent-runtime's stdin."""
    return json.dumps(
        {
            "capability_token": invocation.capability_token,
            "evidence": invocation.evidence.model_dump(mode="json"),
            "config": invocation.config,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def decode_result(raw: str) -> AgentResearchResult:
    """Parse the agent-runtime's single-line result into a proposal + usage.

    A non-``ok`` result raises AgentRuntimeError carrying the runtime's error;
    a produced proposal is re-validated here (defense in depth — the runtime
    already validated, but the Controller never trusts the wire blindly). The
    usage report is carried verbatim; an older proposal-only runtime yields a
    synthetic unavailable report (unknown, not zero).
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentRuntimeError(f"agent-runtime returned non-JSON: {raw[:200]}") from exc
    if not isinstance(payload, dict):
        raise AgentRuntimeError("agent-runtime result is not a JSON object")
    if not payload.get("ok"):
        raise AgentRuntimeError(f"agent-runtime error: {payload.get('error')}")
    proposal = payload.get("proposal")
    if proposal is None:
        raise AgentRuntimeError("agent-runtime result missing proposal")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {
            "source": "unavailable",
            "scope": "unknown",
            "complete": False,
            "incomplete_reasons": ["usage_not_reported"],
        }
    return AgentResearchResult(
        proposal=ResearchProposal.model_validate(proposal),
        usage=usage,
    )


async def run_agent_research(
    invocation: ResearchInvocation,
    *,
    process_factory,
    timeout_seconds: float,
) -> AgentResearchResult:
    """Run one research turn in the agent-runtime subprocess.

    ``process_factory`` is an async callable ``(cmd, env) -> process``
    compatible with ``asyncio.create_subprocess_exec``'s return
    (stdin/stdout pipes). It is injected so the codec + timeout + error
    handling are testable without a real Hermes checkout. Returns the
    proposal plus the usage report decoded from the wire.
    """
    proc = await process_factory()
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(encode_request(invocation).encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise AgentRuntimeError(
            f"agent-runtime research turn exceeded {timeout_seconds}s"
        ) from None
    finally:
        # The subprocess must never outlive this invocation: a timeout, a
        # CancelledError from the worker watchdog, or an error on the
        # communicate path all leave the child running unless we kill it
        # here. ``communicate`` sets returncode on normal completion, so this
        # only fires on the abnormal paths. kill() is synchronous (SIGKILL);
        # the wait is shielded so a re-raise of CancelledError still reaps.
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.shield(proc.wait())
            except asyncio.CancelledError:
                pass  # kill was issued; the OS reaps the child

    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", "replace")[-500:]
        raise AgentRuntimeError(
            f"agent-runtime exited {proc.returncode}: {tail}"
        )
    return decode_result(stdout.decode("utf-8"))


def build_process_factory(
    command: list[str],
    env: dict,
    *,
    cwd: str | None = None,
):
    """Return a process factory that spawns the agent-runtime command with
    the given env (PYTHONPATH pointing at the Hermes checkout, the venv
    interpreter, etc.). Bound here so run_agent_research stays seam-clean."""
    full_env = {**env}

    async def _spawn():
        return await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
            cwd=cwd,
        )

    return _spawn
