"""S06 candidate-package preparation: protocol read + missing-item report.

The prepare entrypoint (ops/prepare_s06_campaign.py) chains the already-tested
panel functions. Its own new logic is small and pure: read the v2 registration
(single source of truth) and report the remaining blocking items honestly.
These tests pin that without a database or a vendor token.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREPARE = REPO_ROOT / "ops" / "prepare_s06_campaign.py"


def _load():
    spec = importlib.util.spec_from_file_location("prepare_s06_campaign", PREPARE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prepare_s06_campaign"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def prepare():
    return _load()


def test_protocol_reads_classification_decided(prepare):
    protocol = prepare._load_protocol()
    assert protocol["status"] == "classification_decided_release_pending_approval"
    assert protocol["sampling"]["stratification_level"] == "eodhd_sector"
    assert protocol["sampling"]["n"] == 20
    assert protocol["formal_campaign_allowed"] is False
    assert protocol["amendment"]["human_release_approval_granted"] is False


def test_protocol_sha256_matches_campaign_policy_v2(prepare):
    import hashlib

    protocol = prepare._load_protocol()
    sha = prepare._protocol_sha256(protocol)
    actual = hashlib.sha256(
        (REPO_ROOT / "docs" / "protocols" / "campaign-policy.v2.md").read_bytes()
    ).hexdigest()
    assert sha == actual == "245fbde10fc90f9fa381ecaf9637d5a1889f149988ba2680fe8d2692c5322ec4"


def test_remaining_missing_reports_blockers(prepare):
    protocol = prepare._load_protocol()
    validation_clean = {"ok": True, "issues": [], "checks": {}}
    items = prepare._remaining_missing(protocol, validation_clean, None, None, None)
    # the persistent gaps are reported regardless of a clean validation
    assert any("source_effective_at_unknown" in i for i in items)
    assert any("tiingo_production_tos_not_confirmed" in i for i in items)
    assert any("formal_model_and_training_manifest_not_registered" in i for i in items)
    assert any("final_release_hash_awaits_human_approval" in i for i in items)
    # a clean validation does NOT add the validation-not-clean blocker
    assert not any("panel_registration_validation_not_clean" in i for i in items)


def test_remaining_missing_flags_unclean_validation(prepare):
    protocol = prepare._load_protocol()
    validation_bad = {"ok": False, "issues": ["frame hash mismatch"], "checks": {}}
    items = prepare._remaining_missing(protocol, validation_bad, None, None, None)
    assert any("panel_registration_validation_not_clean" in i for i in items)
