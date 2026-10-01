"""S06 candidate-package preparation entrypoint.

This is a PREPARATION entrypoint, not the production pipeline. It chains the
already-implemented panel functions (ingest -> frame -> draw -> freeze ->
validate) into one runnable command that produces a reviewable candidate
package for the human approver. It does NOT:

  - set formal_campaign_allowed or fill any approval field,
  - register a campaign,
  - bind a release (the final release hash is produced only after all actual
    references are fixed, and is approved by the project owner — not by this
    tool).

Inputs (environment):
  - EODHD_API_KEY        vendor token (the .env uses this exact name)
  - YOUWEI_DATABASE_URL  asyncpg DSN for the target database

Protocol parameters (seed / n / stratification_level / protocol hash) are
read from docs/protocols/s00-registration.v2.json so there is a single source
of truth, not re-declared values.

Output: a candidate package JSON on stdout (see _candidate_package) with the
frozen registration ids, hashes, the validation report, and the remaining
missing items. Run against a disposable database, never against production,
until the human approval is granted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

from youwei_core.data.eodhd import EODHDClient, ingest_sp500_constituents
from youwei_core.data.panel import (
    build_member_frame,
    draw_panel,
    freeze_panel_registration,
    validate_panel_registration,
)
from youwei_core.db.engine import make_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = REPO_ROOT / "docs" / "protocols" / "s00-registration.v2.json"


def _load_protocol() -> dict:
    return json.loads(REGISTRATION.read_text(encoding="utf-8"))


def _protocol_sha256(protocol: dict) -> str:
    for f in protocol["protocol_files"]:
        if f["path"] == "campaign-policy.v2.md":
            return f["sha256"]
    raise ValueError("campaign-policy.v2.md hash not found in registration")


async def _prepare(args) -> dict:
    token = os.environ.get("EODHD_API_KEY", "").strip()
    database_url = os.environ.get("YOUWEI_DATABASE_URL", "").strip()
    missing = []
    if not token:
        missing.append("EODHD_API_KEY")
    if not database_url:
        missing.append("YOUWEI_DATABASE_URL")
    if missing:
        return {
            "ok": False,
            "error": "missing required environment: " + ", ".join(missing),
            "missing_items": missing,
        }

    protocol = _load_protocol()
    sampling = protocol["sampling"]
    protocol_sha = _protocol_sha256(protocol)

    engine = make_engine(database_url)
    client = EODHDClient(token, min_interval=float(args.min_interval))
    try:
        ingest = await ingest_sp500_constituents(
            engine,
            client,
            index=args.index,
            source_available_at=None,
            source_available_basis=None,
        )
        frame_as_of = args.frame_as_of or date.today()
        frame = await build_member_frame(
            engine, ingest.components, frame_as_of=frame_as_of
        )
        sample = draw_panel(
            frame, seed=sampling["seed"], sample_size=sampling["n"]
        )

        registration = await freeze_panel_registration(
            engine,
            index=args.index.upper(),
            stratification_level=sampling["stratification_level"],
            protocol_ref="campaign-policy-v2",
            protocol_sha256=protocol_sha,
            seed=sampling["seed"],
            sample_size=sampling["n"],
            source_raw_object_id=ingest.raw_object_id,
            observed_at=None,  # vendor exposes no source time
            usable_at=ingest.usable_at,
            source_available_basis="vendor_does_not_expose_source_time",
            frame_as_of=frame_as_of,
            frame=frame,
            sample=sample,
        )

        validation = await validate_panel_registration(
            engine,
            registration.registration_id,
            panel_security_ids=[str(s) for s in sample.selected],
            protocol_sha256=protocol_sha,
        )
    finally:
        await client.aclose()
        await engine.dispose()

    return _candidate_package(
        protocol, ingest, frame, sample, registration, validation, frame_as_of
    )


def _candidate_package(protocol, ingest, frame, sample, registration, validation, frame_as_of) -> dict:
    blocking = protocol.get("blocking_items", [])
    return {
        "ok": validation.get("ok", False),
        "prepared_at_utc": None,  # caller stamps; the tool does not invent time
        "stratification_level": protocol["sampling"]["stratification_level"],
        "classification_decision": protocol.get("classification_decision"),
        "panel_registration": {
            "registration_id": str(registration.registration_id),
            "created": registration.created,
            "frame_as_of": frame_as_of.isoformat(),
            "frame_sha256": frame.frame_sha256,
            "mapping_sha256": registration.mapping_sha256,
            "selected_list_sha256": sample.selected_list_sha256,
            "selected": sample.selected,
            "quotas": sample.quotas,
            "source_raw_object_id": str(ingest.raw_object_id),
        },
        "validation_report": validation,
        "release_candidate": {
            "protocol_ref": "campaign-policy-v2",
            "formal_campaign_allowed": protocol.get("formal_campaign_allowed"),
            "human_approval_granted": False,
            "release_id": None,
            "release_content_hash": None,
        },
        "remaining_missing_items": _remaining_missing(
            protocol, validation, ingest, frame, sample
        ),
    }


def _remaining_missing(protocol, validation, ingest, frame, sample) -> list[str]:
    """The explicit gaps that still block a human-approved release. This is a
    factual report of what is not yet fixed, not a claim that the tool filled
    anything the caller did not supply."""
    items: list[str] = []
    if not validation.get("ok"):
        items.append("panel_registration_validation_not_clean: " + "; ".join(validation.get("issues", [])))
    # source time: EODHD exposes none, so the frame cannot claim a vendor
    # effective time — this is a persistent gap until a timed source exists.
    items.append("source_effective_at_unknown: vendor_does_not_expose_source_time")
    # Tiingo price data is still free/evaluation; production snapshot ToS is
    # not yet confirmed (see docs/research/tiingo-token-verification.md).
    items.append("tiingo_production_tos_not_confirmed")
    # quant-momentum-v0 / baseline-constant-v0 are engineering vehicles, not
    # formally chosen models (see docs/trials/registry.md).
    items.append("formal_model_and_training_manifest_not_registered")
    # final release hash must be produced after every actual reference is
    # fixed and approved by the human owner.
    items.append("final_release_hash_awaits_human_approval")
    return items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="GSPC", help="index code (default GSPC)")
    parser.add_argument("--frame-as-of", type=lambda s: date.fromisoformat(s),
                        default=None, help="frame as-of date (default today)")
    parser.add_argument("--min-interval", type=float, default=1.0,
                        help="EODHD request min interval seconds")
    args = parser.parse_args(argv)

    result = asyncio.run(_prepare(args))
    json.dump(result, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
