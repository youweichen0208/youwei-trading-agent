"""Frozen evidence citations resolve through the public research contract."""

import hashlib
import json

import pytest

from youwei_contracts.research import FrozenEvidence, ResearchReference


SNAPSHOT_ID = "00000000-0000-0000-0000-000000000030"


def frozen_evidence():
    rows = [
        {"security_id": "stock-a", "trade_date": "2026-09-25", "close": "103"},
        {
            "security_id": "stock-a",
            "trade_date": "2026-09-24",
            "close": "101",
            "provenance": {"raw_object_id": "source-before-correction"},
        },
    ]
    content_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return FrozenEvidence(
        run_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        case={
            "case_id": "00000000-0000-0000-0000-000000000003",
            "security_id": "00000000-0000-0000-0000-000000000004",
            "benchmark_security_id": "00000000-0000-0000-0000-000000000005",
            "horizon_td": 20,
            "target_spec_id": "target-spec-v1",
            "target_spec_sha256": "a" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00Z",
            "prediction_deadline_utc": "2026-09-28T13:15:00Z",
            "entry_at_utc": "2026-09-28T13:30:00Z",
            "exit_at_utc": "2026-10-23T20:00:00Z",
        },
        evidence={
            "snapshot_id": SNAPSHOT_ID,
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00Z",
            "mode": "forward",
            "content_sha256": content_hash,
            "content": rows,
            "manifest": {"code_version": "daily-bars-snapshot-v1"},
        },
        target_policy_sha256="a" * 64,
        batch_manifest={},
    )


def test_reference_returns_the_exact_frozen_row_in_original_order():
    from youwei_contracts.research import resolve_reference

    row = resolve_reference(
        frozen_evidence(),
        ResearchReference(kind="evidence", locator=f"snapshot:{SNAPSHOT_ID}/rows/1"),
    )
    assert row == {
        "security_id": "stock-a",
        "trade_date": "2026-09-24",
        "close": "101",
        "provenance": {"raw_object_id": "source-before-correction"},
    }


@pytest.mark.parametrize("locator", [
    "row-0",
    "prices rose yesterday",
    "snapshot:00000000-0000-0000-0000-000000000099/rows/0",
    f"snapshot:{SNAPSHOT_ID}/rows/-1",
    f"snapshot:{SNAPSHOT_ID}/rows/2",
    f"snapshot:{SNAPSHOT_ID}/rows/01",
    f"snapshot:{SNAPSHOT_ID}/rows/+1",
    f"snapshot:{SNAPSHOT_ID}/rows/１",
    f"snapshot:{SNAPSHOT_ID}/rows/0/close",
    f"snapshot:{SNAPSHOT_ID}/rows/0\n",
    f"https://example.test/snapshot:{SNAPSHOT_ID}/rows/0",
])
def test_reference_rejects_unbound_noncanonical_or_missing_rows(locator):
    from youwei_contracts.research import resolve_reference

    with pytest.raises(ValueError):
        resolve_reference(frozen_evidence(), ResearchReference(kind="evidence", locator=locator))


@pytest.mark.parametrize("kind", ["research_memory", "code", "model"])
def test_reference_cannot_treat_another_kind_as_snapshot_evidence(kind):
    from youwei_contracts.research import resolve_reference

    with pytest.raises(ValueError, match="kind"):
        resolve_reference(
            frozen_evidence(),
            ResearchReference(kind=kind, locator=f"snapshot:{SNAPSHOT_ID}/rows/0"),
        )


def test_resolved_row_mutation_cannot_change_the_frozen_input():
    from youwei_contracts.research import resolve_reference

    evidence = frozen_evidence()
    reference = ResearchReference(kind="evidence", locator=f"snapshot:{SNAPSHOT_ID}/rows/1")
    row = resolve_reference(evidence, reference)
    row["provenance"]["raw_object_id"] = "replacement"
    assert resolve_reference(evidence, reference)["provenance"] == {
        "raw_object_id": "source-before-correction"
    }


def test_reference_rejects_evidence_changed_after_deserialization():
    from youwei_contracts.research import resolve_reference

    evidence = frozen_evidence()
    evidence.evidence.content[0]["close"] = "999"
    with pytest.raises(ValueError, match="hash"):
        resolve_reference(
            evidence, ResearchReference(kind="evidence", locator=f"snapshot:{SNAPSHOT_ID}/rows/0")
        )


def test_formatter_exposes_canonical_locators_without_reordering_rows():
    from youwei_contracts.research import evidence_row_locator, resolve_reference

    evidence = frozen_evidence()
    locator = evidence_row_locator(evidence, 1)
    assert locator == f"snapshot:{SNAPSHOT_ID}/rows/1"
    assert resolve_reference(evidence, ResearchReference(kind="evidence", locator=locator))["close"] == "101"


@pytest.mark.parametrize("index", [-1, 2, True, 1.0])
def test_formatter_refuses_nonexistent_or_noninteger_rows(index):
    from youwei_contracts.research import evidence_row_locator

    with pytest.raises(ValueError):
        evidence_row_locator(frozen_evidence(), index)


def proposal_for(evidence, **overrides):
    from youwei_contracts.research import ResearchProposal

    fields = {
        "run_id": evidence.run_id,
        "case_id": evidence.case.case_id,
        "source_status": "produced",
        "p_outperform": 0.6,
        "expected_excess_return": 0.02,
        "references": [{"kind": "evidence", "locator": f"snapshot:{SNAPSHOT_ID}/rows/1"}],
        "model": {"model_version": "test", "provider": "synthetic"},
    }
    fields.update(overrides)
    return ResearchProposal(**fields)


def test_proposal_references_return_rows_for_the_matching_question():
    from youwei_contracts.research import validate_proposal_references

    evidence = frozen_evidence()
    rows = validate_proposal_references(evidence, proposal_for(evidence))
    assert len(rows) == 1
    assert rows[0]["close"] == "101"


@pytest.mark.parametrize("field", ["run_id", "case_id"])
def test_proposal_for_another_question_is_rejected_even_with_valid_locator(field):
    from youwei_contracts.research import validate_proposal_references

    evidence = frozen_evidence()
    proposal = proposal_for(evidence, **{field: "00000000-0000-0000-0000-000000000099"})
    with pytest.raises(ValueError, match=field):
        validate_proposal_references(evidence, proposal)
