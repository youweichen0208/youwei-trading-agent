"""Controller-side FrozenEvidence construction (S07h).

The Controller freezes ONE evidence snapshot per batch at the decision
cutoff (S06a) and hands that bundle to Hermes for the ``llm_adjusted``
position. This module turns the already-frozen snapshot plus one planned
case into the ``research-v1`` ``FrozenEvidence`` wire value that crosses the
process boundary to the agent-runtime.

It is pure logic over the shared ``youwei-contracts`` types: the caller
(``run_batch_predictions``) has already read the case/batch/snapshot rows
with the Controller's own engine and authorization, so this module does no
database I/O and grants nothing. The Controller owns the evidence snapshot
identity; Hermes only ever receives this bounded, content-addressed bundle
(architecture §3.1: FrozenEvidence = data manifest + release + content hash).
"""

from __future__ import annotations

import json
import uuid

from youwei_contracts.research import CasePlan, EvidenceSnapshot, FrozenEvidence

# The agent-runtime's evidence snapshots are daily bars (the only market
# evidence a research run may consume in Phase 1B).
EVIDENCE_KIND = "daily_bars"


class EvidenceAssemblyError(Exception):
    """The batch/case/snapshot rows cannot form a valid FrozenEvidence."""


def build_case_plan(case: dict) -> CasePlan:
    """Build the CasePlan from a planned case row.

    Everything here was fixed when the batch was planned (S05a); Hermes is
    answering a pre-registered question, never choosing one.
    """
    return CasePlan(
        case_id=uuid.UUID(str(case["id"])),
        security_id=uuid.UUID(str(case["security_id"])),
        benchmark_security_id=uuid.UUID(str(case["benchmark_security_id"])),
        horizon_td=int(case["horizon_td"]),
        target_spec_id=case["target_spec_id"],
        target_spec_sha256=case["target_spec_sha256"],
        decision_cutoff_utc=_iso(case["decision_cutoff_utc"]),
        prediction_deadline_utc=_iso(case["prediction_deadline_utc"]),
        entry_at_utc=_iso(case["entry_at_utc"]),
        exit_at_utc=_iso(case["exit_at_utc"]),
    )


def build_frozen_evidence(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    case: dict,
    snapshot: dict,
    batch_manifest: dict,
) -> FrozenEvidence:
    """Assemble the FrozenEvidence bundle for one case.

    ``snapshot`` is the already-read frozen snapshot (``read_snapshot``
    result: ``{id, manifest, content}``). Its content hash is re-verified by
    the contracts ``EvidenceSnapshot`` model on construction; the mode must
    be ``forward`` and as_of <= the case cutoff (the seal path re-checks this
    at write time; here we refuse to hand Hermes a non-forward bundle).

    ``case`` carries ``target_spec_sha256`` (the target policy the campaign
    registered), which becomes ``target_policy_sha256``. The batch manifest
    (calendar version/hash, tzdb) is passed through for the research brief.
    """
    manifest = snapshot["manifest"]
    mode = manifest.get("query", {}).get("mode")
    if mode != "forward":
        raise EvidenceAssemblyError(
            f"evidence snapshot {snapshot['id']} is mode={mode!r}; research may "
            "only consume forward PIT views (historical_source is a reconstruction)"
        )
    as_of = manifest.get("query", {}).get("as_of")
    if as_of is None:
        raise EvidenceAssemblyError(
            f"evidence snapshot {snapshot['id']} has no as_of in its manifest"
        )

    content = snapshot["content"]
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, list):
        raise EvidenceAssemblyError(
            f"evidence snapshot {snapshot['id']} content is not a list of rows"
        )

    return FrozenEvidence(
        contract_version="research-v1",
        run_id=run_id,
        tenant_id=tenant_id,
        case=build_case_plan(case),
        evidence=EvidenceSnapshot(
            snapshot_id=uuid.UUID(str(snapshot["id"])),
            kind=manifest.get("kind", EVIDENCE_KIND),
            as_of=as_of,
            mode="forward",
            content_sha256=manifest["content_sha256"],
            content=content,
            manifest=manifest,
        ),
        target_policy_sha256=case["target_spec_sha256"],
        batch_manifest=batch_manifest,
    )


def _iso(value) -> str:
    if value is None:
        raise EvidenceAssemblyError("missing timestamp in case/batch row")
    # datetimes are already tz-aware in these rows; normalize to ISO
    return value.isoformat()
