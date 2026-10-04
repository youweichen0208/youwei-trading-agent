"""Backfill the feature history window for a named release's panel.

S09c review finding (project owner 2026-10-02): the first batch's
logistic-ridge features need 61 consecutive trading sessions per
security AND the benchmark, and the pipeline reads a 120-calendar-day
window before the cutoff — but the live collection only fetches the
rolling last 5 trading days. Without a backfill, batch 1 would seal
``quant_model=unavailable`` with zero pairable D20 samples.

This script resolves the same fixed collection set as ``collect_tick``
(named release -> panel selected + benchmark, never "latest"), fetches
the requested window from Tiingo once per ticker, and ingests through
the standard ``ingest_daily`` path (idempotent per query+content;
overlapping dates become new observation versions resolved by the PIT
read — the designed versioning mechanism). ``ingested_at``/``usable_at``
keep their actual values: backfill is only legitimate input for batches
whose cutoff is AFTER this run.

A coverage check against the frozen trading calendar reports any
(security, trading-day) hole inside the window.

Run inside a one-shot Core container (core + egress networks):

    docker run --rm --network <env>_core --network <env>_egress \
      -v $PWD/ops/backfill_history.py:/harness/backfill_history.py:ro \
      -e YOUWEI_APP_DB_URL=postgresql+asyncpg://youwei_app:...@postgres:5432/youwei \
      -e YOUWEI_TIINGO_TOKEN=... \
      ghcr.io/youweichen0208/youwei-core@sha256:... \
      python /harness/backfill_history.py --release <id> --start-date 2026-06-01
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy import func, select

from youwei_core.config import Settings
from youwei_core.data.collect import resolve_collection_targets
from youwei_core.data.tiingo import TiingoClient, ingest_daily
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import calendar_days, price_observations


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", required=True, help="named release id")
    ap.add_argument(
        "--start-date", type=date.fromisoformat, required=True,
        help="window start (inclusive); batch 1 needs cutoff-120d = 2026-06-12",
    )
    ap.add_argument(
        "--end-date", type=date.fromisoformat, default=None,
        help="window end (inclusive); default = today",
    )
    args = ap.parse_args()
    end_date = args.end_date or date.today()
    if args.start_date > end_date:
        print("start-date after end-date")
        return 2

    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=1)
    client = TiingoClient(
        settings.tiingo_token,
        base_url=settings.tiingo_base_url,
        min_interval=settings.tiingo_min_request_interval_seconds,
    )
    try:
        targets = await resolve_collection_targets(engine, args.release)
        print(f"targets: {len(targets)} (release {args.release})")
        for t in targets:
            result = await ingest_daily(
                engine,
                client,
                security_id=t.security_id,
                ticker=t.ticker,
                start_date=args.start_date,
                end_date=end_date,
                source_available_at=None,
                source_available_basis=None,
            )
            print(
                f"  {t.ticker:6s} rows={result.rows:4d} "
                f"created={result.created} raw={result.raw_object_id}"
            )
        await client.aclose()

        # Coverage: every trading day in the window must have a bar for
        # every target (PIT-latest; versions collapse in the read).
        async with engine.begin() as conn:
            trading_days = [
                row[0]
                for row in (
                    await conn.execute(
                        select(calendar_days.c.date)
                        .where(
                            calendar_days.c.venue == "XNYS",
                            calendar_days.c.is_trading.is_(True),
                            calendar_days.c.date >= args.start_date,
                            calendar_days.c.date <= end_date,
                        )
                        .order_by(calendar_days.c.date)
                    )
                ).all()
            ]
            have = {
                (row[0], row[1])
                for row in (
                    await conn.execute(
                        select(
                            price_observations.c.security_id,
                            price_observations.c.trade_date,
                        ).where(
                            price_observations.c.security_id.in_(
                                [t.security_id for t in targets]
                            ),
                            price_observations.c.trade_date >= args.start_date,
                            price_observations.c.trade_date <= end_date,
                        )
                    )
                ).all()
            }
            db_now = (await conn.execute(select(func.now()))).scalar_one()
        # A session's bar can only exist after the session completes —
        # only require coverage for trading days strictly before today
        # (UTC date); today's session is the live collection's job.
        completed_days = [d for d in trading_days if d < db_now.date()]
        gaps = [
            (str(t.security_id), t.ticker, d.isoformat())
            for t in targets
            for d in completed_days
            if (t.security_id, d) not in have
        ]
        print(f"calendar trading days in window: {len(trading_days)} "
              f"({len(completed_days)} completed before db now {db_now})")
        if gaps:
            print(f"COVERAGE GAPS: {len(gaps)}")
            for g in gaps[:30]:
                print("  missing:", g)
            return 1
        print("coverage: every target has a bar on every trading day in the window")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
