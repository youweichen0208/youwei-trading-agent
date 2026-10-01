"""S06i: frozen campaign plan (cutoff expansion across DST, two hashes, scope)."""

import uuid
from datetime import UTC, datetime

import pytest

from youwei_core.ledger.plan import (
    CampaignPlanScope,
    expand_planned_cutoffs,
    prepare_campaign_plan,
    scope_manifest_hash,
)


def _first(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc)


def test_cutoffs_advance_local_date_not_utc_hours_across_dst():
    # 2026 US DST starts Sun Mar 8 (spring forward). Mar 7 is EST (UTC-5),
    # Mar 14 is EDT (UTC-4). Advancing 168h in UTC would give Mar 14 11:00 UTC
    # (wrong); advancing the local date keeps 06:00 ET -> 10:00 UTC.
    first = _first("2026-03-07T11:00:00+00:00")  # Sat 06:00 EST
    cutoffs = expand_planned_cutoffs(first, 2)
    assert cutoffs[0] == _first("2026-03-07T11:00:00+00:00")
    assert cutoffs[1] == _first("2026-03-14T10:00:00+00:00")  # Sat 06:00 EDT


def test_cutoffs_fall_back_across_dst():
    # 2026 US DST ends Sun Nov 1 (fall back). Oct 31 is EDT (UTC-4),
    # Nov 7 is EST (UTC-5): 06:00 ET moves from 10:00 to 11:00 UTC.
    first = _first("2026-10-31T10:00:00+00:00")  # Sat 06:00 EDT
    cutoffs = expand_planned_cutoffs(first, 2)
    assert cutoffs[0] == _first("2026-10-31T10:00:00+00:00")
    assert cutoffs[1] == _first("2026-11-07T11:00:00+00:00")


def test_first_cutoff_must_be_saturday_06_et():
    with pytest.raises(ValueError, match="Saturday 06:00"):
        expand_planned_cutoffs(_first("2026-03-06T11:00:00+00:00"), 1)  # Friday


def test_cutoff_count_and_naive_rejection():
    first = _first("2026-03-07T11:00:00+00:00")
    assert len(expand_planned_cutoffs(first, 12)) == 12
    with pytest.raises(ValueError, match="timezone-aware"):
        expand_planned_cutoffs(datetime(2026, 3, 7, 6, 0), 1)
    with pytest.raises(ValueError, match="positive integer"):
        expand_planned_cutoffs(first, 0)


def _plan_kwargs(**overrides):
    base = dict(
        tenant_id=uuid.uuid4(),
        campaign_key="campaign-1",
        release_content_sha256="a" * 64,
        phase="1a",
        first_cutoff=_first("2026-03-07T11:00:00+00:00"),
        batch_count=12,
        panel_security_ids=[str(uuid.uuid4()) for _ in range(20)],
        benchmark_security_id=str(uuid.uuid4()),
        target_specs=[
            {"horizon_td": 1, "target_spec_id": "target-spec-v1", "content_sha256": "b" * 64},
            {"horizon_td": 20, "target_spec_id": "target-spec-v1", "content_sha256": "b" * 64},
            {"horizon_td": 60, "target_spec_id": "target-spec-v1", "content_sha256": "b" * 64},
        ],
        enabled_sources=["baseline", "quant_model"],
        fallback_policy="phase1a-none",
        primary_metric="d20_paired_brier_delta",
        time_protocol_ref="time-protocol.v1",
        time_protocol_sha256="c" * 64,
    )
    base.update(overrides)
    return base


def test_plan_hashes_are_distinct_and_sensitive():
    plan = prepare_campaign_plan(**_plan_kwargs())
    assert plan["planned_cutoffs_sha256"] != plan["campaign_plan_sha256"]
    assert len(plan["planned_cutoffs"]) == 12

    # changing the panel changes the full plan hash but not the cutoff hash
    changed = prepare_campaign_plan(**_plan_kwargs(panel_security_ids=[str(uuid.uuid4())]))
    assert changed["planned_cutoffs_sha256"] == plan["planned_cutoffs_sha256"]
    assert changed["campaign_plan_sha256"] != plan["campaign_plan_sha256"]

    # changing the cutoff list changes both
    shifted = prepare_campaign_plan(**_plan_kwargs(first_cutoff=_first("2026-03-14T10:00:00+00:00")))
    assert shifted["planned_cutoffs_sha256"] != plan["planned_cutoffs_sha256"]
    assert shifted["campaign_plan_sha256"] != plan["campaign_plan_sha256"]


def test_scope_rejects_bad_phase_tenant_and_extra():
    good = dict(
        schema_version=1, kind="campaign_execution", phase="1a",
        tenant_id=str(uuid.uuid4()), campaign_key="campaign-1",
        release_content_sha256="a" * 64, campaign_plan_sha256="b" * 64,
    )
    CampaignPlanScope(**good)  # valid

    with pytest.raises(ValueError):
        CampaignPlanScope(**{**good, "phase": "2a"})
    with pytest.raises(ValueError):
        CampaignPlanScope(**{**good, "kind": "release_only"})
    with pytest.raises(ValueError):
        CampaignPlanScope(**{**good, "release_content_sha256": "zz" * 32})
    with pytest.raises(ValueError):
        CampaignPlanScope(**{**good, "extra": "not-allowed"})


def test_scope_hash_is_stable_and_key_order_independent():
    scope = dict(
        schema_version=1, kind="campaign_execution", phase="1a",
        tenant_id=str(uuid.uuid4()), campaign_key="campaign-1",
        release_content_sha256="a" * 64, campaign_plan_sha256="b" * 64,
    )
    reordered = dict(reversed(list(scope.items())))
    assert scope_manifest_hash(scope) == scope_manifest_hash(reordered)
    assert scope_manifest_hash(CampaignPlanScope(**scope)) == scope_manifest_hash(scope)
