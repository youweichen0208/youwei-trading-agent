"""S09b acceptance harness — real Tiingo collection (round 2).

Restores the frozen panel bundle (20 securities + SPY benchmark) into the
acceptance DB, then collects SPY plus two panel members over the last five
trading days using the REAL Tiingo API, verifying network, credentials,
vendor fields and the actual ingest path. No LLM, no approvals, no
re-sampling of the EODHD panel (the frozen bundle is restored as-is).

Run inside a one-shot Core container on the acceptance ``core`` network
(the DB is internal; the container also needs egress for api.tiingo.com).

Env:
    YOUWEI_APP_DB_URL   postgresql+asyncpg://youwei_app:<pw>@postgres:5432/youwei
    YOUWEI_TIINGO_TOKEN real Tiingo token
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from youwei_core.data.panel_bundle import restore_registration_bundle
from youwei_core.data.tiingo import TiingoClient, ingest_daily
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import (
    calendar_days,
    panel_registrations,
    price_observations,
    securities,
)
from sqlalchemy import select


class Check:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, name, cond, detail=""):
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}  {detail}")

    def summary(self):
        print(f"\n{self.passed} passed, {self.failed} failed")
        return 1 if self.failed else 0


def _last_n_trading_days(n=5):
    end = date.today() - timedelta(days=1)
    days = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[-1], days[0]


async def _ensure_benchmark(engine, bench_id: str, *, ticker: str, valid_from: str):
    """Create the benchmark security with its frozen UUID + ticker identity
    (idempotent). The benchmark is not part of the panel bundle."""
    import uuid as _uuid
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from youwei_core.db.meta import security_identities as _identities

    sid = _uuid.UUID(bench_id)
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(securities)
            .values(id=sid, asset_class="etf", name=f"{ticker} (benchmark)")
            .on_conflict_do_nothing(index_elements=[securities.c.id])
        )
        await conn.execute(
            pg_insert(_identities)
            .values(
                id=_uuid.uuid4(),
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


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel-bundle", default="/bundle/panel-bundle.json")
    ap.add_argument("--db-url", default=os.environ.get("YOUWEI_APP_DB_URL", ""))
    ap.add_argument("--token", default=os.environ.get("YOUWEI_TIINGO_TOKEN", ""))
    ap.add_argument("--members", type=int, default=2, help="panel members to collect (default 2)")
    args = ap.parse_args()

    if not args.db_url or not args.token:
        print("ERROR: --db-url and --token are required", file=sys.stderr)
        return 2

    c = Check()
    bundle = json.loads(Path(args.panel_bundle).read_text())
    engine = make_engine(args.db_url, pool_size=1, max_overflow=0)
    try:
        # 1. restore the frozen panel bundle (validates content hash)
        result = await restore_registration_bundle(engine, bundle)
        c.ok("panel bundle restored", result["validation"]["ok"],
             f"created={result['created']}")
        reg_id = result["registration_id"]

        # 2. build the calendar (needed for later collection scheduling)
        from youwei_core.data.calendar import build_calendar
        await build_calendar(engine, year_start=2016, year_end=2028)
        async with engine.begin() as conn:
            n_days = (await conn.execute(select(calendar_days.c.date))).all()
        c.ok("calendar built 2016-2028", len(n_days) > 0, f"days={len(n_days)}")

        # 3. resolve collection set: benchmark (SPY) + N panel members
        import uuid as _uuid
        from youwei_core.db.meta import security_identities

        refs_path = Path("/bundle/references.json")
        refs = json.loads(refs_path.read_text()) if refs_path.exists() else {}
        bench_id = refs.get("benchmark_security_id")

        # The benchmark (SPY) is NOT in the panel bundle (which carries only
        # the 20 panel members); create it with its frozen UUID + identity.
        if bench_id:
            await _ensure_benchmark(
                engine, bench_id, ticker=refs.get("benchmark_ticker", "SPY"),
                valid_from=refs.get("benchmark_identity_valid_from", "2026-10-01"),
            )

        async with engine.begin() as conn:
            reg = (await conn.execute(select(panel_registrations).where(
                panel_registrations.c.id == reg_id
            ))).mappings().one()
            member_rows = (await conn.execute(
                select(security_identities.c.identifier, security_identities.c.security_id)
                .where(
                    security_identities.c.security_id.in_(
                        [_uuid.UUID(s) for s in reg["selected"][:args.members]]
                    ),
                    security_identities.c.identifier_type == "ticker",
                    security_identities.c.valid_to.is_(None),
                )
            )).all()

        collect = [(r.identifier, r.security_id) for r in member_rows]
        if bench_id:
            collect.append(("SPY", _uuid.UUID(bench_id)))
        c.ok("collection targets resolved",
             len(collect) == args.members + (1 if bench_id else 0),
             f"targets={[(t, str(s)[:8]) for t, s in collect]}")

        # 4. real collection
        start, end = _last_n_trading_days()
        client = TiingoClient(args.token)
        collected = {}
        try:
            for ticker, sid in collect:
                try:
                    r = await ingest_daily(
                        engine, client, security_id=sid, ticker=ticker,
                        start_date=start, end_date=end,
                    )
                    collected[ticker] = r
                    print(f"  collect {ticker}: rows={r.rows} created={r.created}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  collect {ticker}: ERROR {exc}")
                    collected[ticker] = None
        finally:
            await client.aclose()

        for ticker, r in collected.items():
            c.ok(f"{ticker} collected (real)", r is not None and r.rows > 0,
                 f"rows={getattr(r, 'rows', None)}")

        # 5. verify observations landed
        async with engine.begin() as conn:
            for ticker, sid in [(t, s) for t, s in collect]:
                cnt = (await conn.execute(
                    select(price_observations.c.id).where(
                        price_observations.c.security_id == sid
                    )
                )).all()
                c.ok(f"{ticker} observations in DB", len(cnt) > 0, f"rows={len(cnt)}")

    finally:
        await engine.dispose()

    return c.summary()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
