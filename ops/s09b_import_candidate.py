"""S09b candidate import into the production database.

Imports the FROZEN candidate release (release-logistic-ridge-candidate-
20261002-v1, content sha256 bdb8bbe0...) and its training manifest into a
target database, WITHOUT rebuilding them from current sources. Unlike
ops/build_s06_release.py (which re-reads source files and writes the model
artifacts into the input directory), this tool reads the already-frozen
JSON outputs, verifies every reference and hash, and registers idempotently.

Checks before any write:
- release.training_manifest_sha256 == training_manifest_content_hash(tm)
- release.panel_bundle_sha256 == panel bundle content_sha256
- release.code_files (5 files) == current source bytes (sha256)
- release.protocol_refs == current protocol file bytes (sha256)
- release.references == the frozen references.json

Then, in order: restore the panel bundle (validates + inserts securities /
identities / source / raw object / panel registration), create the benchmark
(SPY) with its frozen UUID, build the calendar, register the training
manifest, register the release.

Idempotent: same content re-imports as created=False; different content for
the same id raises (ReleaseConflict / TrainingManifestConflict).

No approvals are created. formal_campaign_allowed stays false. This tool
never starts a worker or enables collection.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from youwei_core.data.panel_bundle import restore_registration_bundle
from youwei_core.data.calendar import build_calendar
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import (
    release_approvals,
    campaigns,
    securities,
    security_identities,
)
from youwei_core.ledger.service import register_release, release_content_hash
from youwei_core.ledger.training import (
    register_training_manifest,
    training_manifest_content_hash,
)


class ImportError_(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json(obj) -> str:
    content = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _ensure_benchmark(engine, bench_id: str, *, ticker: str, valid_from: str):
    sid = uuid.UUID(bench_id)
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(securities)
            .values(id=sid, asset_class="etf", name=f"{ticker} (benchmark)")
            .on_conflict_do_nothing(index_elements=[securities.c.id])
        )
        await conn.execute(
            pg_insert(security_identities)
            .values(
                id=uuid.uuid4(),
                security_id=sid,
                identifier_type="ticker",
                identifier=ticker,
                venue="US",
                valid_from=date.fromisoformat(valid_from),
                valid_to=None,
            )
            .on_conflict_do_nothing(
                index_elements=["identifier_type", "identifier", "venue", "valid_from"]
            )
        )


def verify(release: dict, tm: dict, panel_bundle: dict, refs: dict, repo_root: Path) -> None:
    # 1. training manifest hash linkage
    if release.get("training_manifest_sha256") != training_manifest_content_hash(tm):
        raise ImportError_("release.training_manifest_sha256 != training manifest content hash")
    if release.get("training_manifest_ref") != tm.get("manifest_id"):
        raise ImportError_("release.training_manifest_ref != training manifest id")

    # 2. panel bundle hash linkage
    if release.get("panel_bundle_sha256") != panel_bundle.get("content_sha256"):
        raise ImportError_("release.panel_bundle_sha256 != panel bundle content_sha256")
    reg = panel_bundle["payload"]["registration"]
    if release.get("panel_registration_id") != reg["id"]:
        raise ImportError_("release.panel_registration_id != panel bundle registration id")

    # 3. code_files must match current source bytes
    for path, expected in release.get("code_files", {}).items():
        actual = sha256_file(repo_root / path)
        if actual != expected:
            raise ImportError_(f"code_files[{path}] mismatch: expected {expected}, got {actual}")

    # 4. protocol_refs must match current protocol file bytes
    for path, expected in release.get("protocol_refs", {}).items():
        actual = sha256_file(repo_root / "docs" / "protocols" / path)
        if actual != expected:
            raise ImportError_(f"protocol_refs[{path}] mismatch: expected {expected}, got {actual}")

    # 5. references must equal the frozen references.json
    if sha256_json(release.get("references")) != sha256_json(refs):
        raise ImportError_("release.references != frozen references.json")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate-dir", required=True)
    ap.add_argument("--db-url", default=os.environ.get("YOUWEI_DATABASE_URL", ""))
    ap.add_argument("--repo-root", default="/app")
    args = ap.parse_args()

    if not args.db_url:
        print("ERROR: --db-url (or YOUWEI_DATABASE_URL) is required", file=sys.stderr)
        return 2

    root = Path(args.candidate_dir)
    repo = Path(args.repo_root)
    release = json.loads((root / "release-candidate.json").read_text())
    tm = json.loads((root / "training-manifest.json").read_text())
    panel_bundle = json.loads((root / "panel-bundle.json").read_text())
    refs = json.loads((root / "references.json").read_text())

    # Reject approval/campaign-bearing imports outright.
    if release.get("status") != "candidate_unapproved":
        raise ImportError_(f"refusing to import release with status {release.get('status')!r}")

    verify(release, tm, panel_bundle, refs, repo)

    engine = make_engine(args.db_url, pool_size=1, max_overflow=0)
    try:
        # restore panel bundle (validates content hash + inserts)
        bundle_result = await restore_registration_bundle(engine, panel_bundle)
        print(f"panel bundle: ok={bundle_result['validation']['ok']} "
              f"created={bundle_result['created']}")

        # benchmark + calendar
        bench = release["references"]
        await _ensure_benchmark(
            engine, bench["benchmark_security_id"],
            ticker=bench["benchmark_ticker"],
            valid_from=bench["benchmark_identity_valid_from"],
        )
        cal = release["references"]
        await build_calendar(
            engine, year_start=cal["calendar_years"][0], year_end=cal["calendar_years"][1],
        )
        print(f"benchmark + calendar ready (calendar {cal['calendar_version']})")

        # training manifest + release (idempotent)
        tm_rec = await register_training_manifest(engine, manifest=tm)
        rel_rec = await register_release(
            engine, release_id=release["release_id"], manifest=release
        )
        print(f"training manifest: id={tm_rec.manifest_id} "
              f"sha256={tm_rec.content_sha256} created={tm_rec.created}")
        print(f"release: id={rel_rec.release_id} "
              f"sha256={rel_rec.release_content_sha256} created={rel_rec.created}")

        # confirm no approvals / campaigns were created
        async with engine.begin() as conn:
            n_approvals = len((await conn.execute(select(release_approvals.c.id))).all())
            n_campaigns = len((await conn.execute(select(campaigns.c.id))).all())
        print(f"approval records: {n_approvals}; campaigns: {n_campaigns}")
        if n_approvals or n_campaigns:
            print("WARNING: unexpected approval/campaign rows present", file=sys.stderr)

    finally:
        await engine.dispose()

    print("import complete (no approval, no campaign)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
