"""research-v1: Controller-frozen evidence and Hermes research proposal.

Point-in-time discipline (architecture section 4 / S05e): the Controller
freezes ONE evidence snapshot per batch at the decision cutoff, before any
research runs. Hermes receives that frozen bundle and returns a proposal for
the ``llm_adjusted`` source position only. Hermes never writes the Ledger,
never changes a Lesson, and never sees data past the case cutoff: the frozen
evidence is the *only* market input, and the proposal is validated and sealed
by the Controller.

A proposal is a *proposal* — not a seal. The Controller re-checks fencing,
window, and evidence discipline at seal time exactly as for the quant and
baseline positions. This file is a wire contract with no database or
upstream SDK imports.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --- frozen evidence (Controller -> Hermes) -------------------------------


class CasePlan(WireModel):
    """The prediction question Hermes must answer, frozen by the Controller.

    Everything here was fixed when the batch was planned (S05a); Hermes is
    answering a pre-registered question, not choosing one.
    """

    case_id: uuid.UUID
    security_id: uuid.UUID
    benchmark_security_id: uuid.UUID
    horizon_td: Literal[1, 20, 60]
    target_spec_id: str
    target_spec_sha256: str
    decision_cutoff_utc: str
    prediction_deadline_utc: str
    entry_at_utc: str
    exit_at_utc: str


class EvidenceSnapshot(WireModel):
    """A frozen PIT snapshot reference + its content, bounded to one batch.

    ``content_sha256`` must equal sha256(canonical JSON of ``content``);
    ``as_of`` and ``mode`` are asserted by the Controller before handing the
    bundle to Hermes (mode must be ``forward`` and as_of <= cutoff).
    """

    snapshot_id: uuid.UUID
    kind: str
    as_of: str
    mode: Literal["forward"]
    content_sha256: str
    content: list[dict]  # daily bars; canonical JSON round-trips byte-identical
    manifest: dict

    @model_validator(mode="after")
    def verify_content_hash(self):
        data = json.dumps(
            self.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        actual = hashlib.sha256(data).hexdigest()
        if self.content_sha256 != actual:
            raise ValueError("evidence content hash mismatch")
        return self


class FrozenEvidence(WireModel):
    """The complete, read-only research input for one case.

    A single frozen snapshot is shared across the batch (S06a), so the same
    ``evidence`` object is handed to every case of that batch; only
    ``case`` differs.
    """

    contract_version: Literal["research-v1"] = "research-v1"
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    case: CasePlan
    evidence: EvidenceSnapshot
    target_policy_sha256: str  # target-spec content hash the campaign registered
    batch_manifest: dict  # BatchTimes manifest incl. calendar version/hash


# --- research proposal (Hermes -> Controller) -----------------------------


class ResearchReference(WireModel):
    """A citable source location, resolvable back to the frozen evidence or
    an approved, as_of-bound ResearchMemory entry. No free-text-only claims."""

    kind: Literal["evidence", "research_memory", "code", "model"]
    locator: str  # e.g. snapshot row key, memory entry id, module+version, model id
    note: str | None = None


class ResearchWarning(WireModel):
    kind: Literal["insufficient_history", "missing_data", "low_confidence", "other"]
    detail: str


class ProposalModel(WireModel):
    """The actual model used for the llm_adjusted position. Versioned and
    attributable; the Controller records it into the prediction."""

    model_version: str
    provider: str
    # cost is reported for attribution; the authoritative ledger entry is
    # recorded by the Controller/budget layer, never trusted from Hermes.
    cost_estimate: dict = Field(default_factory=dict)


class ResearchProposal(WireModel):
    """Hermes's research output for the llm_adjusted position.

    Value discipline mirrors sealing.py SourcePrediction: the Controller
    rejects partial/unavailable-with-value mismatches and out-of-range
    probabilities exactly as it does for quant/baseline.
    """

    contract_version: Literal["research-v1"] = "research-v1"
    run_id: uuid.UUID
    case_id: uuid.UUID
    source_status: Literal["produced", "unavailable"]
    reason: str | None = None
    p_outperform: float | None = None
    expected_excess_return: float | None = None
    references: list[ResearchReference] = Field(default_factory=list)
    warnings: list[ResearchWarning] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    quantitative_basis: str | None = None
    model: ProposalModel | None = None

    @model_validator(mode="after")
    def value_discipline(self):
        if self.source_status == "produced":
            if self.p_outperform is None or self.expected_excess_return is None:
                raise ValueError("produced proposal requires both values")
            if not (0.0 <= self.p_outperform <= 1.0):
                raise ValueError("p_outperform must be in [0, 1]")
            if self.model is None:
                raise ValueError("produced proposal requires an attributable model")
        else:  # unavailable
            if self.p_outperform is not None or self.expected_excess_return is not None:
                raise ValueError("unavailable proposal must not carry values")
            if self.reason is None:
                raise ValueError("unavailable proposal requires a reason")
        return self


def proposal_digest(proposal: ResearchProposal) -> str:
    """Canonical digest for idempotency/attribution (mirrors request_digest)."""
    value = json.dumps(
        proposal.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(value.encode()).hexdigest()
