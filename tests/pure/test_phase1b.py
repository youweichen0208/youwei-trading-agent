"""S07g: Phase 1B sealing policy and the pipeline proposal seam (offline).

The Phase 1B policy (campaign-policy section 3) is now registered in the
sealing path. These tests pin _validate_sources acceptance/rejection and the
pipeline's llm_adjusted provider mapping, using a mock campaign object — no
database.
"""

import uuid
from types import SimpleNamespace

import pytest

from youwei_contracts.research import ResearchProposal
from youwei_core.ledger.sealing import (
    SourcePrediction,
    SourceValidationError,
    _validate_sources,
)
from youwei_core.ledger.pipeline import (
    _phase1a_llm_adjusted,
    make_phase1b_llm_adjusted_provider,
)

SNAP = uuid.uuid4()


def _campaign(enabled=None, fallback=None):
    return SimpleNamespace(
        enabled_sources=enabled or ["baseline", "quant_model", "llm_adjusted"],
        fallback_policy=fallback or "phase1b-llm-from-quant",
    )


def _pred(source, status, *, p=None, e=None, reason=None, model=None):
    return SourcePrediction(
        source=source,
        source_status=status,
        p_outperform=p,
        expected_excess_return=e,
        reason=reason,
        evidence_snapshot_id=SNAP if status in ("produced", "fallback") else None,
        model_version=model,
    )


def _proposal(status="produced", **overrides):
    base = {
        "run_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "source_status": status,
        "p_outperform": 0.6,
        "expected_excess_return": 0.02,
        "references": [],
        "model": {"model_version": "llm-v1", "provider": "test"},
    }
    base.update(overrides)
    if status != "produced":
        base["p_outperform"] = None
        base["expected_excess_return"] = None
        base["reason"] = base.get("reason", "insufficient_history")
        base["model"] = None
    return ResearchProposal(**base)


# --- _validate_sources -------------------------------------------------------


def test_phase1b_accepts_three_produced_positions():
    campaign = _campaign()
    entries = _validate_sources(
        [
            _pred("baseline", "produced", p=0.5, e=0.0),
            _pred("quant_model", "produced", p=0.4, e=0.01, model="qm"),
            _pred("llm_adjusted", "produced", p=0.6, e=0.02, model="llm"),
        ],
        campaign,
    )
    assert {e["source"] for e in entries} == {
        "baseline", "quant_model", "llm_adjusted",
    }


def test_phase1b_accepts_fallback_when_policy_matches():
    campaign = _campaign()
    entries = _validate_sources(
        [
            _pred("baseline", "produced", p=0.5, e=0.0),
            _pred("quant_model", "produced", p=0.4, e=0.01, model="qm"),
            _pred("llm_adjusted", "fallback", p=0.4, e=0.01, reason="timeout", model="qm"),
        ],
        campaign,
    )
    llm = next(e for e in entries if e["source"] == "llm_adjusted")
    assert llm["source_status"] == "fallback"


def test_phase1b_rejects_fallback_with_phase1a_policy():
    campaign = _campaign(
        enabled=["baseline", "quant_model", "llm_adjusted"],
        fallback="phase1a-none",
    )
    with pytest.raises(SourceValidationError, match="fallback"):
        _validate_sources(
            [
                _pred("baseline", "produced", p=0.5, e=0.0),
                _pred("quant_model", "produced", p=0.4, e=0.01, model="qm"),
                _pred("llm_adjusted", "fallback", p=0.4, e=0.01, reason="timeout", model="qm"),
            ],
            campaign,
        )


def test_phase1b_rejects_unregistered_source_set():
    # a source set that is neither Phase 1A nor Phase 1B
    campaign = _campaign(enabled=["baseline", "llm_adjusted"])
    with pytest.raises(SourceValidationError, match="no registered"):
        _validate_sources(
            [
                _pred("baseline", "produced", p=0.5, e=0.0),
                _pred("quant_model", "produced", p=0.4, e=0.01, model="qm"),
                _pred("llm_adjusted", "produced", p=0.6, e=0.02, model="llm"),
            ],
            campaign,
        )


def test_phase1a_still_fixes_llm_adjusted_at_unavailable_not_enabled():
    campaign = _campaign(
        enabled=["baseline", "quant_model"], fallback="phase1a-none"
    )
    with pytest.raises(SourceValidationError, match="not_enabled"):
        _validate_sources(
            [
                _pred("baseline", "produced", p=0.5, e=0.0),
                _pred("quant_model", "produced", p=0.4, e=0.01, model="qm"),
                _pred("llm_adjusted", "produced", p=0.6, e=0.02, model="llm"),
            ],
            campaign,
        )


# --- pipeline provider seam --------------------------------------------------


async def test_phase1a_provider_fixes_unavailable_not_enabled():
    pred = await _phase1a_llm_adjusted(None, None, None, None)
    assert pred.source == "llm_adjusted"
    assert pred.source_status == "unavailable"
    assert pred.reason == "not_enabled"


async def test_phase1b_provider_maps_produced_proposal():
    async def fetch(case, bars):
        return _proposal("produced")

    provider = make_phase1b_llm_adjusted_provider(fetch)
    quant = _pred("quant_model", "produced", p=0.4, e=0.01, model="qm")
    pred = await provider(None, None, quant, SNAP)
    assert pred.source == "llm_adjusted"
    assert pred.source_status == "produced"
    assert pred.p_outperform == 0.6
    assert pred.model_version == "llm-v1"
    assert pred.evidence_snapshot_id == SNAP


async def test_phase1b_provider_falls_back_to_quant_on_unavailable():
    async def fetch(case, bars):
        return _proposal("unavailable", reason="timeout")

    provider = make_phase1b_llm_adjusted_provider(fetch)
    quant = _pred("quant_model", "produced", p=0.4, e=0.01, model="qm")
    pred = await provider(None, None, quant, SNAP)
    assert pred.source_status == "fallback"
    assert pred.p_outperform == 0.4  # copied from quant
    assert pred.reason == "timeout"


async def test_phase1b_provider_marks_runtime_error_unavailable():
    async def fetch(case, bars):
        raise RuntimeError("agent runtime down")

    provider = make_phase1b_llm_adjusted_provider(fetch)
    quant = _pred("quant_model", "produced", p=0.4, e=0.01, model="qm")
    pred = await provider(None, None, quant, SNAP)
    assert pred.source_status == "unavailable"
    assert "agent_runtime_error" in pred.reason
    assert pred.p_outperform is None
