"""Deterministic Hermes adapter surface (S07 third slice).

This module holds the parts of the Hermes adapter that are pure logic and
therefore testable offline: the isolation configuration that MUST be applied
to every research run, and the deterministic FrozenEvidence -> research
brief transformation that feeds Hermes a single, reproducible prompt.

The actual ``AIAgent`` invocation (gateway, model, tool calls) lives in a
later slice and runs only on the SG host, where the pinned Hermes checkout
and Python 3.14 venv exist. Nothing here imports Hermes; it depends only on
the ``youwei-contracts`` research types and the standard library.
"""

from __future__ import annotations

import json

from youwei_contracts.research import FrozenEvidence

# --- isolation configuration (architecture section 10 / runtime pinning) ---

# Every research run must disable Hermes's implicit memory, session search,
# background review, and context-file discovery. The values below are the
# constructor kwargs verified against the pinned commit (7fa45eb...):
#   AIAgent(skip_memory=True, skip_context_files=True,
#           skip_background_review=True, enabled_toolsets=[])
# `enabled_toolsets=[]` (empty, NOT None) selects NO built-in toolset, so
# Hermes cannot reach terminal/file/browser/web/session_search. Platform
# tools (snapshot_manifest / quant_run / sandbox_submit / ...) are registered
# separately and are the ONLY tools a research run may call.
ISOLATION_KWARGS: dict = {
    "skip_memory": True,
    "skip_context_files": True,
    "skip_background_review": True,
    "enabled_toolsets": [],
    "disabled_toolsets": [],  # belt-and-suspenders: explicit empty deny
}

# The research-tool whitelist (architecture section 10). These are platform
# tools, not Hermes built-in toolsets; the adapter exposes only these to the
# research role. Each is server-side authorized against the run's capability
# scopes and the frozen evidence.
RESEARCH_TOOLS: tuple[str, ...] = (
    "snapshot_manifest",
    "quant_run",
    "sandbox_submit",
    "sandbox_status",
    "artifact_read",
)


# --- deterministic research brief ------------------------------------------


def build_research_brief(evidence: FrozenEvidence) -> str:
    """Render the frozen evidence into a single, deterministic research brief.

    The brief is the only market input Hermes sees. It is derived strictly
    from the frozen bundle (which is content-addressed and PIT-frozen), so a
    re-run of the same run_id/case over the same snapshot yields the same
    brief. No wall-clock time, no live data, no mutable state.
    """
    case = evidence.case
    ev = evidence.evidence
    bars = ev.content

    # The case's own bars (matching the case security), sorted by trade date.
    sec = str(case.security_id)
    own_bars = sorted(
        (b for b in bars if b.get("security_id") == sec),
        key=lambda b: b.get("trade_date", ""),
    )
    closes = [b.get("close") for b in own_bars if b.get("close") is not None]

    lines = [
        f"Research brief (research-v1, run {evidence.run_id})",
        "",
        "## Frozen evidence",
        f"- snapshot_id: {ev.snapshot_id}",
        f"- kind: {ev.kind}",
        f"- as_of: {ev.as_of}",
        f"- mode: {ev.mode}",
        f"- content_sha256: {ev.content_sha256}",
        "",
        "## Prediction question",
        f"- case_id: {case.case_id}",
        f"- security_id: {case.security_id}",
        f"- benchmark_security_id: {case.benchmark_security_id}",
        f"- horizon: D{case.horizon_td}",
        f"- target_spec: {case.target_spec_id} ({case.target_spec_sha256[:12]}...)",
        f"- decision_cutoff: {case.decision_cutoff_utc}",
        f"- entry: {case.entry_at_utc}",
        f"- exit: {case.exit_at_utc}",
        "",
        "## Observed bars (own security)",
    ]
    if own_bars:
        lines.append(f"- bar count: {len(own_bars)}")
        if closes:
            lines.append(
                f"- first close: {closes[0]}, last close: {closes[-1]}"
            )
    else:
        lines.append("- bar count: 0 (no usable observations at as_of)")

    lines += [
        "",
        "## Task",
        "Produce a forward probability p_outperform and expected excess return",
        "for this security vs the benchmark over the stated horizon, grounded",
        "ONLY in the frozen evidence above. Cite each quantitative claim with a",
        "reference (row locator) resolvable back to the evidence. If the evidence",
        "is insufficient, return source_status=unavailable with a reason and the",
        "missing fields — never fabricate a probability.",
    ]
    return "\n".join(lines)


def proposal_from_payload(run_id, case_id, payload: dict):
    """Construct a ResearchProposal from an adapter payload, enforcing the
    wire discipline declared in youwei_contracts.research. Returns the
    validated proposal or raises ValueError on a discipline violation.

    This is the boundary where Hermes's unstructured output is shaped into a
    Controller-checkable value; the Controller still re-validates at seal.
    """
    from youwei_contracts.research import ResearchProposal

    return ResearchProposal(
        run_id=run_id,
        case_id=case_id,
        **payload,
    )
