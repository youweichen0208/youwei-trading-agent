"""Verify quant prediction coverage through the REAL batch read path.

S09c review finding (project owner 2026-10-02): "collection succeeded,
batch sealed" does not prove the experiment is scorable — the quant
source must actually produce ``available`` predictions. This script
exercises the same code path a batch uses at cutoff time:

  1. loads the release manifest and the frozen calendar build from the
     production database,
  2. builds the ReleasePredictor exactly like the pipeline
     (``build_release_predictor``: artifact hashes, feature contract,
     training-cutoff sanity, frozen calendar, tzdb pin),
  3. reads bars with ``daily_bars_asof`` (the PIT read behind
     ``freeze_daily_bars``; mode=forward, latest usable version per
     security/day) — read-only, no snapshot is written,
  4. runs ``predict_case`` for every panel security x horizon {1,20,60}
     at a SIMULATED cutoff (default: the last completed session close)
     and asserts every quant prediction is available with complete
     features,
  5. reports the batch-1 forward window: the 61 sessions the real
     cutoff (2026-10-10) will require, which of them already have bars,
     and which are still pending on the live rolling collection.

The simulated cutoff cannot be in the future (forward-mode PIT read
rejects as_of > now), so today's verification proves the mechanism on
the fully-backfilled 61-day window ending at the last completed
session; the remaining sessions before the real cutoff arrive via the
live collection before 2026-10-10 06:00 ET.

Run inside a one-shot Core container (core network only):
    docker run --rm --network <env>_core \
      -v $PWD/ops/verify_prediction_coverage.py:/harness/verify.py:ro \
      -e YOUWEI_APP_DB_URL=postgresql+asyncpg://youwei_app:...@postgres:5432/youwei \
      ghcr.io/youweichen0208/youwei-core@sha256:... \
      python /harness/verify.py --release <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from youwei_core.config import Settings
from youwei_core.data.collect import resolve_collection_targets
from youwei_core.data.pit import daily_bars_asof
from youwei_core.data.calendar import tzdb_version
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import calendar_builds, research_releases
from youwei_core.ledger.model_registry import build_release_predictor

REAL_CUTOFF = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)  # batch 1, frozen plan
LOOKBACK_DAYS = 120  # ReleasePredictor.lookback_calendar_days (MODEL_VERSION)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", required=True)
    ap.add_argument(
        "--sim-cutoff", type=datetime.fromisoformat, default=None,
        help="simulated cutoff (tz-aware); default = last completed session close",
    )
    ap.add_argument(
        "--real-cutoff", type=datetime.fromisoformat, default=REAL_CUTOFF,
        help="forward-looking cutoff for the coverage report (default: batch 1)",
    )
    args = ap.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with engine.begin() as conn:
            manifest = (
                await conn.execute(
                    select(research_releases.c.manifest).where(
                        research_releases.c.release_id == args.release
                    )
                )
            ).scalar_one()
            calendar = (
                await conn.execute(
                    select(
                        calendar_builds.c.version,
                        calendar_builds.c.content_sha256,
                        calendar_builds.c.content,
                    ).order_by(calendar_builds.c.generated_at.desc())
                )
            ).mappings().first()
            db_now = (await conn.execute(select(func.now()))).scalar_one()

        sim_cutoff = args.sim_cutoff
        if sim_cutoff is None:
            # last session whose close has already happened at db_now
            # (a not-yet-completed session would lack bars by definition)
            from zoneinfo import ZoneInfo
            et = ZoneInfo("America/New_York")
            last_session = None
            for d in json.loads(calendar["content"]):
                if not d["is_trading"]:
                    continue
                session_date = date.fromisoformat(d["date"])
                close_local = datetime(
                    session_date.year, session_date.month, session_date.day,
                    13 if d["early_close"] else 16, 0, tzinfo=et,
                )
                if close_local.astimezone(timezone.utc) <= db_now:
                    last_session = session_date
            if last_session is None:
                print("no completed session before db now")
                return 2
            sim_cutoff = datetime(
                last_session.year, last_session.month, last_session.day,
                20, 0, tzinfo=timezone.utc,
            )  # regular 16:00 ET close (EDT); early closes are rare and
            # only tighten the session filter, never loosen it
        if sim_cutoff.tzinfo is None:
            print("sim-cutoff must be timezone-aware")
            return 2
        print(f"simulated cutoff: {sim_cutoff.isoformat()} (db now {db_now})")

        batch_manifest = {
            "tzdb_version": tzdb_version(),
            "calendar_version": calendar["version"],
            "calendar_sha256": calendar["content_sha256"],
        }
        targets = await resolve_collection_targets(engine, args.release)
        panel = [t for t in targets]
        benchmark_id = manifest["references"]["benchmark_security_id"]
        panel_ids = [t for t in panel if str(t.security_id) != benchmark_id]

        predictor = await build_release_predictor(
            engine,
            release_manifest=manifest,
            batch_manifest=batch_manifest,
            cutoff=sim_cutoff,
            benchmark_security_id=benchmark_id,
        )

        start_date = (sim_cutoff - timedelta(days=LOOKBACK_DAYS)).date()
        bars = await daily_bars_asof(
            engine,
            [t.security_id for t in panel],
            start_date,
            sim_cutoff.date(),
            as_of=db_now,
            mode="forward",
        )
        bars_by_security: dict[str, list[dict]] = {}
        for bar in bars:
            bars_by_security.setdefault(str(bar["security_id"]), []).append(bar)
        for bs in bars_by_security.values():
            bs.sort(key=lambda b: b["trade_date"])
        print(f"bars read: {len(bars)} rows, {len(bars_by_security)} securities, "
              f"window {start_date}..{sim_cutoff.date()}")

        unavailable = []
        available = 0
        for t in panel_ids:
            for horizon in (1, 20, 60):
                case = {"security_id": str(t.security_id), "horizon_td": horizon}
                pred = predictor.predict_case(case, bars_by_security)
                if pred.source_status == "unavailable":
                    unavailable.append((t.ticker, horizon, pred.reason))
                else:
                    available += 1
        total = len(panel_ids) * 3
        print(f"quant predictions: {available}/{total} available "
              f"({len(panel_ids)} securities x 3 horizons)")
        for u in unavailable:
            print("  UNAVAILABLE:", u)
        if unavailable:
            return 1

        # Forward coverage report for the real cutoff: the 61 sessions
        # the feature window will require, split into already-covered
        # vs pending on the live rolling collection.
        sessions = sorted(
            date.fromisoformat(s["date"]) for s in json.loads(calendar["content"]) if s["is_trading"]
        )
        needed = [d for d in sessions if d <= args.real_cutoff.date()][-61:]
        have_dates = {bar["trade_date"] for bar in bars}  # ISO strings
        covered = [d for d in needed if d.isoformat() in have_dates]
        pending = [d for d in needed if d.isoformat() not in have_dates]
        print(f"\nbatch-1 window @ {args.real_cutoff.isoformat()}: "
              f"{len(needed)} sessions ({needed[0]} .. {needed[-1]})")
        print(f"  already covered: {len(covered)} sessions (through {max(covered)})")
        print(f"  pending on live collection: {len(pending)} "
              f"({', '.join(d.isoformat() for d in pending)})")
        print("  (live collection runs ET 17:30-05:30 daily; all pending "
              "sessions land before the Saturday 06:00 ET cutoff)")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
