"""One-shot research invocation (S07m): the agent-runtime subprocess boundary.

The Controller (Core worker) submits one research invocation per case through
the Runner's controlled interface; the research container reads a single JSON
request on stdin and writes a single JSON result on stdout. This module holds
the request/result codec and the Ed25519 grant check as pure logic (no Hermes
import), so it is testable without the pinned checkout; the actual ``AIAgent``
turn is delegated to ``runtime.run_research``.

Trust model (architecture §10, hardened): the Controller signs an Ed25519
research grant (aud=runtime-research) binding invocation/tenant/run/job/
attempt/case/evidence/config with scopes and an expiry. This process holds
only the PUBLIC key (by kid) and VERIFIES the grant before calling the
gateway — it can never sign one. The grant is the ONLY thing this process
accepts; request parameters alone never prove authority.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    ResearchCapabilityError,
    verify_research_token,
)
from youwei_contracts.research import FrozenEvidence, ResearchProposal

# The capability scope a research run must carry (signed by the Controller's
# worker loop when it claims the prediction job).
REQUIRED_SCOPE = SCOPE_RESEARCH_RUN
REQUIRED_AUDIENCE = AUD_RUNTIME_RESEARCH


class InvocationError(Exception):
    """The request cannot be honored (malformed, unauthorized, or a
    research-turn failure)."""


@dataclass(frozen=True)
class ResearchRequest:
    capability_token: str
    evidence: FrozenEvidence
    config: dict  # runtime.ResearchConfig fields, validated by the runtime


def _check_capability(
    public_keys: dict[str, str], token: str, evidence: FrozenEvidence
) -> None:
    """Verify the Controller's Ed25519 research grant against the evidence.

    The research container holds only PUBLIC keys (by kid); it can verify the
    grant but never sign one. The grant must be the runtime-research audience,
    carry the research:run scope, and bind the evidence's tenant AND case
    (stronger than the legacy HMAC check, which only matched tenant).
    """
    try:
        cap = verify_research_token(public_keys, token)
    except ResearchCapabilityError as exc:
        raise InvocationError(f"research capability rejected: {exc}") from exc
    if cap.aud != REQUIRED_AUDIENCE:
        raise InvocationError(
            f"research capability has wrong audience (expected {REQUIRED_AUDIENCE!r})"
        )
    if REQUIRED_SCOPE not in cap.scopes:
        raise InvocationError(
            f"research capability missing required scope {REQUIRED_SCOPE!r}"
        )
    if cap.tenant_id != evidence.tenant_id:
        raise InvocationError(
            "research capability tenant does not match the evidence bundle's tenant"
        )
    if cap.case_id != evidence.case.case_id:
        raise InvocationError(
            "research capability case does not match the evidence bundle's case"
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

    ``turn`` is a ``runtime.ResearchTurn`` (proposal OR experiment request +
    usage report). The wire carries the usage report verbatim (labeled with
    source/scope/complete) so the Controller can settle actual cost; cost
    settlement itself lives in the Controller/budget layer.
    """
    usage = turn.usage.to_dict() if hasattr(turn.usage, "to_dict") else dict(turn.usage or {})
    payload = {
        "ok": True,
        "usage": usage,
    }
    if getattr(turn, "experiment_request", None) is not None:
        payload["experiment_request"] = turn.experiment_request.model_dump(mode="json")
    else:
        payload["proposal"] = turn.proposal.model_dump(mode="json")
    return json.dumps(
        payload,
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
    public_keys: dict[str, str],
    run_research,  # runtime.run_research (deferred import keeps this pure-logic)
    config_factory,  # runtime.ResearchConfig
) -> str:
    """Handle one decoded request: verify the grant, construct the
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
    _check_capability(public_keys, token, evidence)

    config = config_factory(**config_raw)
    turn = await run_research(
        evidence, config,
        capability_token=token,
        public_keys=public_keys,
    )
    # The wire now carries the turn's usage report alongside the proposal
    # (source/scope/complete + token counters) for Controller cost settlement.
    return encode_result(turn)
