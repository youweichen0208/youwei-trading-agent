"""S09b: create or reuse the production research tenant.

Idempotent by slug (NOT by id): the production tenant is identified by the
slug ``youwei-internal-research``. If a tenant with that slug already exists,
its UUID is reused; otherwise a new UUID is created once. The resolved UUID
is printed so it can be recorded for the campaign plan hash.

Run against the production database via the app (DML) role.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from youwei_core.db.engine import make_engine
from youwei_core.db.meta import tenants

SLUG = "youwei-internal-research"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", default=os.environ.get("YOUWEI_DATABASE_URL", ""))
    ap.add_argument("--slug", default=SLUG)
    args = ap.parse_args()
    if not args.db_url:
        print("ERROR: --db-url is required", file=sys.stderr)
        return 2

    engine = make_engine(args.db_url, pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as conn:
            existing = (await conn.execute(
                select(tenants.c.id).where(tenants.c.slug == args.slug)
            )).scalar_one_or_none()
        if existing is not None:
            print(f"reused existing tenant {args.slug}: {existing}")
            return 0

        tenant_id = uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                pg_insert(tenants)
                .values(id=tenant_id, slug=args.slug)
                .on_conflict_do_nothing(index_elements=[tenants.c.id])
            )
        # confirm and report the authoritative id (re-read in case a
        # concurrent create won)
        async with engine.begin() as conn:
            final = (await conn.execute(
                select(tenants.c.id).where(tenants.c.slug == args.slug)
            )).scalar_one()
        print(f"created tenant {args.slug}: {final}")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
