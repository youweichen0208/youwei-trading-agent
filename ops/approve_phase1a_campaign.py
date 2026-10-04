"""Register the owner's Phase 1A campaign approval and the campaign.

Executes approval package §6 (docs/ops/phase1a-campaign-approval-20261002.md,
r1) after the project owner's explicit confirmation ("批准", 2026-10-02):

  1. ``approve_release`` — records the human approval with the structured
     scope manifest (CampaignPlanScope) binding tenant + campaign key +
     release content hash + campaign plan hash.
  2. ``register_campaign`` — registers phase1a-pilot-2026q4 with the exact
     frozen parameters. The service recomputes the full plan hash from
     the ACTUAL registration parameters and rejects any drift from the
     approved hash; it also requires the approval scope to bind this
     exact plan.

Every parameter is re-derived from the single sources of truth (the
registration protocol and the S06j frozen constants, identical to
ops/s09b_make_plan.py) and pinned against the expected values from the
approval package BEFORE anything is written. Idempotent: re-running
re-submits the same approval and registration without changes.

Run inside a one-shot Core container on the production core network
(no vendor calls, no egress needed).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import research_releases, tenants
from youwei_core.ledger.plan import CampaignPlanScope, prepare_campaign_plan, scope_manifest_hash
from youwei_core.ledger.service import approve_release, register_campaign

REPO_ROOT = Path(__file__).resolve().parents[1]


def _registration_path() -> Path:
    """The registration protocol: repo checkout, or the one-shot
    container's read-only mount at /harness/s00-registration.v2.json."""
    candidates = [
        REPO_ROOT / "docs" / "protocols" / "s00-registration.v2.json",
        Path("/harness/s00-registration.v2.json"),
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"registration protocol not found in {candidates}")

# --- pinned expected values (approval package r1; mismatch = abort) -------
EXPECTED_PLAN_SHA = "d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29"
EXPECTED_CUTOFFS_SHA = "59f7aa3e1303c75da6d1795f5353628ec8dc9e54b78fcd48edbce12a3b290788"
EXPECTED_SCOPE_SHA = "0b899b003c923efca10dc7de63f4921c1e87ad5223f610caa4ee602689a11805"
EXPECTED_RELEASE_SHA = "bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994"

# --- S06j frozen inputs (identical to ops/s09b_make_plan.py) ---------------
CAMPAIGN_KEY = "phase1a-pilot-2026q4"
RELEASE_ID = "release-logistic-ridge-candidate-20261002-v1"
PHASE = "1a"
FIRST_CUTOFF = "2026-10-10T06:00:00-04:00"
BATCH_COUNT = 12
PRIMARY_METRIC = "paired_brier_quant_minus_baseline"
TIME_PROTOCOL_REF = "time-protocol-v1"
TIME_PROTOCOL_SHA = "82d6b3473d8ce609f138e476d157c7032c3818753cd50c4b404ac568952cf8fe"
TENANT_SLUG = "youwei-internal-research"
EXPECTED_TENANT_ID = "f497c122-45b6-497b-bb99-9c42401c3e5f"

APPROVER = "human-owner"
SCOPE_TEXT = (
    "phase1a-pilot-2026q4: approve release bdb8bbe0… per campaign plan "
    "d403c8e5… for the 12-batch forward experiment (approval package r1, "
    "2026-10-02)"
)
BASIS = "phase1a-pilot-2026q4 one-time approval 2026-10-02 (package r1)"


def _target_specs(protocol: dict) -> list[dict]:
    target_spec_sha = None
    for f in protocol.get("protocol_files", []):
        if f["path"] == "target-spec.v1.md":
            target_spec_sha = f["sha256"]
            break
    if not target_spec_sha:
        raise ValueError("target-spec.v1.md hash not found in registration")
    return [
        {"horizon_td": h, "target_spec_id": f"excess-tr-d{h}-v1",
         "content_sha256": target_spec_sha}
        for h in protocol["target"]["horizons_td"]
    ]


async def main() -> int:
    protocol = json.loads(_registration_path().read_text())
    panel_ids = protocol["sampling"]["selected_security_ids"]
    benchmark_id = protocol["target"]["benchmark_security_id"]
    target_specs = _target_specs(protocol)

    plan = prepare_campaign_plan(
        tenant_id=uuid.UUID(EXPECTED_TENANT_ID),
        campaign_key=CAMPAIGN_KEY,
        release_content_sha256=EXPECTED_RELEASE_SHA,
        phase=PHASE,
        first_cutoff=datetime.fromisoformat(FIRST_CUTOFF),
        batch_count=BATCH_COUNT,
        panel_security_ids=panel_ids,
        benchmark_security_id=benchmark_id,
        target_specs=target_specs,
        enabled_sources=["baseline", "quant_model"],
        fallback_policy="phase1a-none",
        primary_metric=PRIMARY_METRIC,
        time_protocol_ref=TIME_PROTOCOL_REF,
        time_protocol_sha256=TIME_PROTOCOL_SHA,
    )
    scope = CampaignPlanScope(
        phase=PHASE,
        tenant_id=uuid.UUID(EXPECTED_TENANT_ID),
        campaign_key=CAMPAIGN_KEY,
        release_content_sha256=EXPECTED_RELEASE_SHA,
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )

    # ---- pins: derived values must equal the approved package exactly ----
    checks = [
        ("campaign_plan_sha256", plan["campaign_plan_sha256"], EXPECTED_PLAN_SHA),
        ("planned_cutoffs_sha256", plan["planned_cutoffs_sha256"], EXPECTED_CUTOFFS_SHA),
        ("scope_manifest_sha256", scope_manifest_hash(scope), EXPECTED_SCOPE_SHA),
    ]
    for name, got, want in checks:
        if got != want:
            print(f"ABORT: derived {name} {got} != approved {want}")
            return 2
        print(f"pin OK: {name} = {got[:16]}…")

    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with engine.begin() as conn:
            tenant = (
                await conn.execute(
                    select(tenants.c.id).where(tenants.c.slug == TENANT_SLUG)
                )
            ).scalar_one()
            if str(tenant) != EXPECTED_TENANT_ID:
                print(f"ABORT: tenant {TENANT_SLUG} is {tenant}, expected {EXPECTED_TENANT_ID}")
                return 2
            release = (
                await conn.execute(
                    select(
                        research_releases.c.release_content_sha256,
                        research_releases.c.manifest,
                    ).where(research_releases.c.release_id == RELEASE_ID)
                )
            ).mappings().one()
            if release["release_content_sha256"] != EXPECTED_RELEASE_SHA:
                print(f"ABORT: release content hash {release['release_content_sha256']}")
                return 2
            panel_registration_id = release["manifest"]["panel_registration_id"]
        print(f"release {RELEASE_ID} content hash OK; panel registration {panel_registration_id}")

        # ---- 1. record the owner's approval ------------------------------
        scope_sha = await approve_release(
            engine,
            release_id=RELEASE_ID,
            approver_principal_id=APPROVER,
            scope=SCOPE_TEXT,
            scope_manifest=scope.model_dump(mode="json"),
            basis=BASIS,
        )
        print(f"approval recorded: approver={APPROVER} scope_sha256={scope_sha}")

        # ---- 2. register the campaign ------------------------------------
        record = await register_campaign(
            engine,
            tenant_id=uuid.UUID(EXPECTED_TENANT_ID),
            campaign_key=CAMPAIGN_KEY,
            release_id=RELEASE_ID,
            target_specs=target_specs,
            time_protocol_ref=TIME_PROTOCOL_REF,
            time_protocol_sha256=TIME_PROTOCOL_SHA,
            benchmark_security_id=uuid.UUID(benchmark_id),
            panel_security_ids=[str(s) for s in panel_ids],
            panel_manifest={"panel_registration_id": panel_registration_id},
            enabled_sources=["baseline", "quant_model"],
            fallback_policy="phase1a-none",
            primary_metric=PRIMARY_METRIC,
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )
        print(
            f"campaign registered: id={record.campaign_id} "
            f"chain={record.chain_id} created={record.created}"
        )
        print(f"planned cutoffs: {len(plan['planned_cutoffs'])} "
              f"({plan['planned_cutoffs'][0]} .. {plan['planned_cutoffs'][-1]})")
        print("done")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
