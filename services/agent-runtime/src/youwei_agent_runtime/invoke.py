"""One-shot research invocation (S07h): the agent-runtime subprocess boundary.

The Controller (Core worker) launches this package as a short-lived
subprocess for one ``llm_adjusted`` research turn: it writes a single JSON
request to stdin and reads a single JSON result from stdout. This module
holds the request/result codec and the capability-token check as pure logic
(no Hermes import), so it is testable without the pinned checkout; the actual
``AIAgent`` turn is delegated to ``runtime.run_research``.

Trust model (architecture §10): the Controller signs a per-job capability
token (bound to job/attempt/tenant with scopes and an expiry). This process
verifies it before calling the gateway, so a request's parameters alone never
prove authority. The token is the ONLY thing this process accepts.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from youwei_contracts.capability import CapabilityError, verify_capability
from youwei_contracts.research import FrozenEvidence, ResearchProposal

# The capability scope a research run must carry (signed by the Controller's
# worker loop when it claims the prediction job).
REQUIRED_SCOPE = "llm_call"


class InvocationError(Exception):
    """The request cannot be honored (malformed, unauthorized, or a
    research-turn failure)."""


@dataclass(frozen=True)
class ResearchRequest:
    capability_token: str
    evidence: FrozenEvidence
    config: dict  # runtime.ResearchConfig fields, validated by the runtime


def _check_capability(secret: str, token: str, evidence: FrozenEvidence) -> None:
    """Verify the Controller's capability token against the evidence bundle.

    The token binds job/attempt/tenant; the evidence carries run_id and
    tenant_id. We require the scope and tenant match and the token be
    unexpired. (The job/attempt binding is the Controller's own check at
    claim time; here the tenant and scope are what this process can verify.)
    """
    try:
        cap = verify_capability(secret, token)
    except CapabilityError as exc:
        raise InvocationError(f"capability rejected: {exc}") from exc
    if REQUIRED_SCOPE not in cap.scopes:
        raise InvocationError(
            f"capability missing required scope {REQUIRED_SCOPE!r}"
        )
    if cap.tenant_id != evidence.tenant_id:
        raise InvocationError(
            "capability tenant does not match the evidence bundle's tenant"
        )


def decode_request(raw: str) -> dict:
    """Decode one request line. Returns the raw payload dict; capability
    and evidence validation happen in ``honor``."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvocationError("request is not JSON") from exc
    if not isinstance(payload, dict):
        raise InvocationError("request must be a JSON object")
    return payload


def encode_result(turn) -> str:
    """Serialize a successful research turn for the Controller to parse.

    ``turn`` is a ``runtime.ResearchTurn`` (proposal + usage report). The wire
    carries the usage report verbatim (labeled with source/scope/complete) so
    the Controller can settle actual cost; cost settlement itself lives in the
    Controller/budget layer.
    """
    usage = turn.usage.to_dict() if hasattr(turn.usage, "to_dict") else dict(turn.usage or {})
    return json.dumps(
        {
            "ok": True,
            "proposal": turn.proposal.model_dump(mode="json"),
            "usage": usage,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def encode_error(exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


async def honor_request(
    payload: dict,
    *,
    capability_secret: str,
    run_research,  # runtime.run_research (deferred import keeps this pure-logic)
    config_factory,  # runtime.ResearchConfig
) -> str:
    """Handle one decoded request: verify the capability, construct the
    evidence + config, run the research turn, and encode the result.

    ``run_research`` and ``config_factory`` are injected so this module stays
    importable without the Hermes checkout (see runtime.py)."""
    if not isinstance(payload, dict):
        raise InvocationError("request must be a JSON object")
    token = payload.get("capability_token")
    evidence_raw = payload.get("evidence")
    config_raw = payload.get("config", {})
    if not token or not isinstance(token, str):
        raise InvocationError("missing capability_token")
    if evidence_raw is None:
        raise InvocationError("missing evidence bundle")

    evidence = FrozenEvidence.model_validate(evidence_raw)
    _check_capability(capability_secret, token, evidence)

    config = config_factory(**config_raw)
    turn = await run_research(
        evidence, config,
        capability_token=token,
        capability_secret=capability_secret,
    )
    # The wire now carries the turn's usage report alongside the proposal
    # (source/scope/complete + token counters) for Controller cost settlement.
    return encode_result(turn)
