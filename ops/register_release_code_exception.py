"""Register a release code exception (bounded compatibility acceptance).

Owner decision 2026-10-03 (review of the S12 D2 enablement prep): an
accepted difference between an approved release's registered ``code_files``
and the code actually deployed is recorded per (release, deployed image
digest) in the append-only ``release_code_exceptions`` table:

- the ACTUAL code_files hashes of the deployed build,
- the diff summary and why the campaign's sealing path is unaffected,
- the verification evidence (tests, commands, document reference + hash),
- approver + decision basis; ``created_at`` is the server clock at insert —
  approval times are never backdated, and a difference discovered after
  deployment (as with core r4) is recorded as such in the basis.

Scope is explicit per row: future deployments do NOT inherit an exception.
UPDATE/DELETE are blocked by trigger; re-running with identical content is a
no-op, different content under the same (release, digest) aborts.

Run inside a one-shot Core container on the core network (same pattern as
ops/approve_phase1a_campaign.py):

    docker run --rm --network <env>_core \\
      -v $PWD/ops/register_release_code_exception.py:/harness/register.py:ro \\
      -e YOUWEI_DATABASE_URL=postgresql+asyncpg://youwei_app:...@postgres:5432/youwei \\
      <core image> python /harness/register.py \\
        --release-id <id> --campaign-id <uuid> \\
        --image-digest sha256:... \\
        --code-files-json '<file->sha map of the DEPLOYED build>' \\
        --diff-summary '<text>' --verification-json '<evidence json>' \\
        --approver human-owner --basis '<decision text>'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import select

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import campaigns, release_code_exceptions, research_releases


def _required_json(value: str, name: str):
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--{name} is not valid JSON: {exc}")
    if not isinstance(parsed, dict) or not parsed:
        raise SystemExit(f"--{name} must be a non-empty JSON object")
    return parsed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release-id", required=True)
    ap.add_argument("--campaign-id", required=True)
    ap.add_argument("--image-digest", required=True,
                    help="registry digest of the DEPLOYED image (sha256:...)")
    ap.add_argument("--code-files-json", required=True,
                    help="JSON object file->sha256 of the deployed build's code_files")
    ap.add_argument("--diff-summary", required=True)
    ap.add_argument("--verification-json", required=True,
                    help="JSON object with the verification evidence")
    ap.add_argument("--approver", required=True)
    ap.add_argument("--basis", required=True)
    args = ap.parse_args()

    code_files = _required_json(args.code_files_json, "code-files-json")
    verification = _required_json(args.verification_json, "verification-json")
    for name, v in code_files.items():
        if not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
            raise SystemExit(f"code_files[{name!r}] must be a 64-hex sha256")
    if not args.image_digest.startswith("sha256:") or len(args.image_digest) != 71:
        raise SystemExit("--image-digest must be sha256:<64 hex>")

    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with engine.begin() as conn:
            release = (
                await conn.execute(
                    select(research_releases).where(
                        research_releases.c.release_id == args.release_id
                    )
                )
            ).mappings().one_or_none()
            if release is None:
                print(f"ABORT: release {args.release_id!r} not found")
                return 2
            campaign = (
                await conn.execute(
                    select(campaigns).where(
                        campaigns.c.id == uuid.UUID(args.campaign_id)
                    )
                )
            ).mappings().one_or_none()
            if campaign is None:
                print(f"ABORT: campaign {args.campaign_id!r} not found")
                return 2

            existing = (
                await conn.execute(
                    select(release_code_exceptions).where(
                        release_code_exceptions.c.release_row_id == release.id,
                        release_code_exceptions.c.deployed_image_digest == args.image_digest,
                    )
                )
            ).mappings().one_or_none()
            if existing is not None:
                same = (
                    existing.code_files_actual == code_files
                    and existing.diff_summary == args.diff_summary
                    and existing.verification == verification
                    and existing.approver == args.approver
                    and existing.decision_basis == args.basis
                    and existing.campaign_id == campaign.id
                )
                if same:
                    print(f"already registered (idempotent): {args.image_digest[:19]}…")
                    print(f"registered_at: {existing.created_at.isoformat()}")
                    return 0
                print(
                    "ABORT: an exception for (release, digest) exists with DIFFERENT "
                    "content; the table is append-only — investigate instead of "
                    "re-registering"
                )
                return 2

            await conn.execute(
                release_code_exceptions.insert().values(
                    release_row_id=release.id,
                    campaign_id=campaign.id,
                    deployed_image_digest=args.image_digest,
                    code_files_actual=code_files,
                    diff_summary=args.diff_summary,
                    verification=verification,
                    approver=args.approver,
                    decision_basis=args.basis,
                )
            )
            row = (
                await conn.execute(
                    select(release_code_exceptions.c.created_at).where(
                        release_code_exceptions.c.release_row_id == release.id,
                        release_code_exceptions.c.deployed_image_digest == args.image_digest,
                    )
                )
            ).scalar_one()
            print(f"registered: release={args.release_id}")
            print(f"  campaign={campaign.campaign_key} ({campaign.id})")
            print(f"  deployed_image_digest={args.image_digest}")
            print(f"  created_at={row.isoformat()} (server clock; not backdatable)")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
