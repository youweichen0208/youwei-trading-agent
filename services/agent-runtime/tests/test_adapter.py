"""Deterministic Hermes adapter surface — offline tests.

No Hermes runtime, no LLM, no gateway, no database. These pin the isolation
configuration and the FrozenEvidence -> research brief transformation that
the research role MUST apply, and the proposal shaping boundary.
"""

import hashlib
import json
import uuid

import pytest

from youwei_agent_runtime.adapter import (
    ISOLATION_KWARGS,
    RESEARCH_TOOLS,
    build_research_brief,
    proposal_from_payload,
)


def _bars(sec: str, closes=(100.0, 101.0, 102.0)):
    return [
        {
            "security_id": sec,
            "trade_date": f"2026-09-{22 + i:02d}",
            "close": c,
            "volume": 1000,
            "provenance": {"raw_object_id": str(uuid.uuid4())},
        }
        for i, c in enumerate(closes)
    ]


def _content_sha(bars):
    data = json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def _frozen(sec="00000000-0000-0000-0000-00000000000a", bars=None):
    from youwei_contracts.research import FrozenEvidence

    bars = _bars(sec) if bars is None else bars
    return FrozenEvidence(
        run_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        case={
            "case_id": str(uuid.uuid4()),
            "security_id": sec,
            "benchmark_security_id": "00000000-0000-0000-0000-00000000000b",
            "horizon_td": 20,
            "target_spec_id": "target-spec-v1",
            "target_spec_sha256": "c" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00+00:00",
            "prediction_deadline_utc": "2026-09-28T13:15:00+00:00",
            "entry_at_utc": "2026-09-28T13:30:00+00:00",
            "exit_at_utc": "2026-10-23T20:00:00+00:00",
        },
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00+00:00",
            "mode": "forward",
            "content_sha256": _content_sha(bars),
            "content": bars,
            "manifest": {"code_version": "daily-bars-snapshot-v1"},
        },
        target_policy_sha256="d" * 64,
        batch_manifest={"calendar_version": "nyse-rules-v1"},
    )


def test_isolation_disables_implicit_state():
    # skip_memory + skip_context_files + skip_background_review all True, and
    # ONLY the platform research toolset is enabled (never a Hermes built-in).
    assert ISOLATION_KWARGS["skip_memory"] is True
    assert ISOLATION_KWARGS["skip_context_files"] is True
    assert ISOLATION_KWARGS["skip_background_review"] is True
    assert ISOLATION_KWARGS["enabled_toolsets"] == ["youwei-research"]
    # the whitelist is exactly the platform research tools, not Hermes built-ins
    assert set(RESEARCH_TOOLS) == {
        "snapshot_manifest", "quant_run", "sandbox_submit",
        "sandbox_status", "artifact_read",
    }


def test_brief_is_deterministic_for_same_evidence():
    ev = _frozen()
    b1 = build_research_brief(ev)
    b2 = build_research_brief(ev)
    assert b1 == b2


def test_brief_contains_only_frozen_facts():
    ev = _frozen(sec="00000000-0000-0000-0000-00000000000a")
    brief = build_research_brief(ev)
    # no wall clock / live data: the brief is derived from the frozen bundle
    assert str(ev.run_id) in brief
    assert ev.evidence.content_sha256 in brief
    assert "D20" in brief
    assert "last close: 102.0" in brief
    # the task must forbid fabrication
    assert "never fabricate" in brief


def test_brief_reports_zero_bars_without_fabricating():
    ev = _frozen(bars=[])
    brief = build_research_brief(ev)
    assert "bar count: 0" in brief


def test_brief_publishes_exact_rows_with_resolvable_original_positions():
    from youwei_contracts.research import ResearchReference, resolve_reference

    sec = "00000000-0000-0000-0000-00000000000a"
    own = _bars(sec)
    other = _bars("00000000-0000-0000-0000-00000000000b", closes=(200.0,))
    ev = _frozen(sec=sec, bars=[other[0], own[2], own[0], own[1]])
    brief = build_research_brief(ev)
    # Citeable rows use the original snapshot order, including other panel
    # securities if present; the summary's sorting never renumbers them.
    records = [json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":')]
    assert len(records) == 4
    assert records[0]["locator"] == f"snapshot:{ev.evidence.snapshot_id}/rows/0"
    assert records[0]["row"]["close"] == 200.0
    assert records[1]["row"]["close"] == 102.0
    assert records[3]["row"]["close"] == 101.0
    for record in records:
        assert resolve_reference(
            ev, ResearchReference(kind="evidence", locator=record["locator"])
        ) == record["row"]


def test_proposal_from_payload_produces_valid_proposal():
    payload = {
        "source_status": "produced",
        "p_outperform": 0.6,
        "expected_excess_return": 0.02,
        "model": {"model_version": "m1", "provider": "p1"},
        "references": [{"kind": "evidence", "locator": "row-0"}],
        "warnings": [],
        "missing": [],
        "quantitative_basis": "momentum",
    }
    p = proposal_from_payload(uuid.uuid4(), uuid.uuid4(), payload)
    assert p.source_status == "produced"
    assert p.p_outperform == 0.6


def test_proposal_from_payload_rejects_bad_value():
    with pytest.raises(Exception):
        proposal_from_payload(
            uuid.uuid4(), uuid.uuid4(),
            {"source_status": "produced", "p_outperform": 1.5,
             "expected_excess_return": 0.0,
             "model": {"model_version": "m", "provider": "p"}},
        )
