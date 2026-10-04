"""S09b: generate the campaign_plan_sha256 and approval scope_manifest.

Reads the frozen S06j forward-plan inputs (campaign key, release hash, panel,
benchmark, target specs, time protocol, first cutoff) plus the just-created
production tenant UUID, and produces the two hashes that the approval package
will bind:

- ``campaign_plan_sha256``: full experiment scope (S06i §2).
- ``scope_manifest``: the structured approval scope (CampaignPlanScope).

Pure: no database writes. It re-derives every hash from the frozen inputs so
the output can be independently re-checked. The panel (selected_security_ids)
and benchmark come from docs/protocols/s00-registration.v2.json (single source
of truth); the release hash and first_cutoff/batch_count are the S06j frozen
inputs.

No approval is recorded here; formal_campaign_allowed stays false.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from youwei_core.ledger.plan import (
    CampaignPlanScope,
    prepare_campaign_plan,
    scope_manifest_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = REPO_ROOT / "docs" / "protocols" / "s00-registration.v2.json"

# S06j frozen inputs (project owner 2026-10-02).
CAMPAIGN_KEY = "phase1a-pilot-2026q4"
RELEASE_SHA = "bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994"
PHASE = "1a"
FIRST_CUTOFF = "2026-10-10T06:00:00-04:00"  # Saturday 06:00 America/New_York (EDT)
BATCH_COUNT = 12
PRIMARY_METRIC = "paired_brier_quant_minus_baseline"
TIME_PROTOCOL_REF = "time-protocol-v1"
TIME_PROTOCOL_SHA = "82d6b3473d8ce609f138e476d157c7032c3818753cd50c4b404ac568952cf8fe"


def _target_specs(protocol: dict) -> list[dict]:
    """target_specs from the protocol: one per horizon, each bound to the
    target-spec.v1.md content hash (all horizons share the same spec file)."""
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--out", default=None, help="write JSON to this path")
    args = ap.parse_args()

    protocol = json.loads(REGISTRATION.read_text())
    panel_ids = protocol["sampling"]["selected_security_ids"]
    benchmark_id = protocol["target"]["benchmark_security_id"]
    target_specs = _target_specs(protocol)

    plan = prepare_campaign_plan(
        tenant_id=args.tenant_id,
        campaign_key=CAMPAIGN_KEY,
        release_content_sha256=RELEASE_SHA,
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
        tenant_id=args.tenant_id,
        campaign_key=CAMPAIGN_KEY,
        release_content_sha256=RELEASE_SHA,
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )

    result = {
        "tenant_id": args.tenant_id,
        "campaign_key": CAMPAIGN_KEY,
        "release_content_sha256": RELEASE_SHA,
        "phase": PHASE,
        "first_cutoff": plan["first_cutoff"],
        "batch_count": BATCH_COUNT,
        "planned_cutoffs": plan["planned_cutoffs"],
        "planned_cutoffs_sha256": plan["planned_cutoffs_sha256"],
        "campaign_plan_sha256": plan["campaign_plan_sha256"],
        "primary_metric": PRIMARY_METRIC,
        "time_protocol_ref": TIME_PROTOCOL_REF,
        "time_protocol_sha256": TIME_PROTOCOL_SHA,
        "scope_manifest": scope.model_dump(mode="json"),
        "scope_manifest_sha256": scope_manifest_hash(scope),
        "panel_count": len(panel_ids),
        "benchmark_security_id": benchmark_id,
        "formal_campaign_allowed": False,
        "human_approval_granted": False,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
