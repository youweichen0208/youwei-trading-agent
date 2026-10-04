"""Shared S06i test helper: build a frozen campaign plan and its structured
approval scope (prepare -> scope manifest -> register order).

This lives in a normal module (NOT conftest.py) because several test files
import it directly, and a ``from conftest import ...`` resolves to
``tests/pure/conftest.py`` once the pure directory is on sys.path during a
full-repo collection — the repo rule is to pass test configuration through
fixtures and never import a module named conftest.
"""

from __future__ import annotations

from datetime import UTC, datetime

from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.ledger.plan import CampaignPlanScope, prepare_campaign_plan


def make_campaign_plan(
    *,
    tenant_id,
    campaign_key,
    release_content_sha256,
    benchmark_security_id,
    panel_security_ids,
    target_specs,
    time_protocol_sha256,
    first_cutoff=None,
    batch_count=12,
    phase="1a",
    fallback_policy="phase1a-none",
    enabled_sources=None,
):
    """Build a frozen campaign plan and its structured approval scope.

    Default anchor: the FIRST coming Saturday cutoff. The 12-week frozen
    plan therefore starts in the future and contains exactly the cutoffs the
    scheduler/plan_batch tests plan against, with no past weeks to backfill.
    (A fixed 2026-01-03 anchor ended the plan in March 2026 and rejected the
    Sept/Oct-2026 cutoffs; an anchor BEFORE now backfilled past weeks.)"""
    first = first_cutoff or next_weekly_cutoff(datetime.now(UTC))
    plan = prepare_campaign_plan(
        tenant_id=tenant_id,
        campaign_key=campaign_key,
        release_content_sha256=release_content_sha256,
        phase=phase,
        first_cutoff=first,
        batch_count=batch_count,
        panel_security_ids=[str(s) for s in panel_security_ids],
        benchmark_security_id=str(benchmark_security_id),
        target_specs=target_specs,
        enabled_sources=enabled_sources or ["baseline", "quant_model"],
        fallback_policy=fallback_policy,
        primary_metric="d20_paired_brier_delta",
        time_protocol_ref="time-protocol-v1",
        time_protocol_sha256=time_protocol_sha256,
    )
    scope = CampaignPlanScope(
        phase=phase,
        tenant_id=tenant_id,
        campaign_key=campaign_key,
        release_content_sha256=release_content_sha256,
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )
    return plan, scope
