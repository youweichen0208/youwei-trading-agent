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
import re
import uuid
from copy import deepcopy
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
    locator: str  # evidence: snapshot:<uuid>/rows/<index>; other kinds need their own resolver
    note: str | None = None


def evidence_row_locator(evidence: FrozenEvidence, row_index: int) -> str:
    """Name a row at its original position in the frozen content array."""
    if type(row_index) is not int or not 0 <= row_index < len(evidence.evidence.content):
        raise ValueError("row index must be an existing nonnegative integer")
    return f"snapshot:{evidence.evidence.snapshot_id}/rows/{row_index}"


def resolve_reference(evidence: FrozenEvidence, reference: ResearchReference) -> dict:
    """Return a detached row from the caller's already-authorized evidence.

    Locators are ``snapshot:<snapshot_id>/rows/<zero-based index>`` into
    the original, hash-bound content array, never a sorted or filtered view.
    This performs no I/O and grants no database/tenant authorization.
    """
    if reference.kind != "evidence":
        raise ValueError("unsupported reference kind; expected evidence")
    match = re.fullmatch(
        rf"snapshot:{evidence.evidence.snapshot_id}/rows/(0|[1-9][0-9]*)",
        reference.locator,
    )
    if match is None:
        raise ValueError("reference must name a canonical row in this snapshot")
    row_index = int(match.group(1))
    if row_index >= len(evidence.evidence.content):
        raise ValueError("reference row does not exist in this snapshot")
    # Nested JSON is mutable even after Pydantic deserialization.
    evidence.evidence.verify_content_hash()
    return deepcopy(evidence.evidence.content[row_index])


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


def resolve_experiment_reference(
    experiments: "list", reference: "ResearchReference"
) -> dict:
    """Resolve a kind="code" reference against the EXPERIMENT outcomes
    carried in this turn's request (S08 re-entry turns). The locator must
    name an artifact manifest of one of the carried experiments; the
    Controller independently re-verifies against its accepted records at
    seal, so this is the runtime-side half of the check."""
    from youwei_contracts.experiment import (
        ExperimentContext,
        parse_experiment_locator,
    )

    if reference.kind != "code":
        raise ValueError(
            f"unsupported reference kind; expected code, got {reference.kind!r}"
        )
    try:
        experiment_id, computation_id, path = parse_experiment_locator(
            reference.locator
        )
    except ValueError as exc:
        raise ValueError(f"invalid experiment locator: {exc}") from exc
    for exp in experiments:
        context = (
            exp
            if isinstance(exp, ExperimentContext)
            else ExperimentContext.model_validate(exp)
        )
        if context.experiment_invocation_id != experiment_id:
            continue
        for computation in context.computations:
            if computation.computation_id != computation_id:
                continue
            for artifact in computation.artifacts:
                if artifact.path == path:
                    return {
                        "experiment_invocation_id": str(experiment_id),
                        "computation_id": str(computation_id),
                        "artifact": artifact.model_dump(mode="json"),
                    }
    raise ValueError(
        "experiment reference does not name an artifact of the carried experiments"
    )


def validate_proposal_references(
    evidence: FrozenEvidence,
    proposal: ResearchProposal,
    *,
    experiments: "list | None" = None,
) -> list[dict]:
    """Check question binding and resolve citations against trusted inputs.

    snapshot references resolve against the frozen evidence (kind="evidence").
    When ``experiments`` (accepted outcomes carried on the request, S08) is
    provided, kind="code" references resolve against their artifact
    manifests; without carried experiments, kind="code" is rejected. Other
    reference kinds need a separately authorized resolver and are rejected
    here. This checks cited locations, not whether the claims follow from
    those rows. The Controller must still authorize the bundle and enforce
    its seal gates."""
    if proposal.run_id != evidence.run_id:
        raise ValueError("proposal run_id does not match frozen evidence")
    if proposal.case_id != evidence.case.case_id:
        raise ValueError("proposal case_id does not match frozen evidence")
    evidence.evidence.verify_content_hash()
    resolved = []
    for reference in proposal.references:
        if reference.kind == "evidence":
            resolved.append(resolve_reference(evidence, reference))
        elif reference.kind == "code" and experiments:
            resolved.append(resolve_experiment_reference(experiments, reference))
        else:
            raise ValueError(
                f"unsupported reference kind {reference.kind!r}; "
                "no authorized resolver for it in this turn"
            )
    return resolved


def proposal_digest(proposal: ResearchProposal) -> str:
    """Canonical digest for idempotency/attribution (mirrors request_digest)."""
    value = json.dumps(
        proposal.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(value.encode()).hexdigest()
