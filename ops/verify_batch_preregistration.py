"""Read-only batch-1 preregistration check for the Phase 1A campaign.

Owner checklist (2026-10-03, implementation plan "next actions" item 1):
after the scheduler preregisters batch 1 (first tick past 2026-10-03
06:00 ET), verify READ-ONLY that

  1. the right campaign has exactly one registered batch,
  2. the batch carries exactly 60 cases,
  3. the sealed windows match the frozen calendar,
  4. there are no duplicate batches/cases.

Sanity extras: the frozen 12-cutoff plan is intact, the panel/benchmark/
target specs match the campaign registration, the batch is a forward
plan (not backfilled), and no commits exist before the cutoff.

Window expectations are derived through the SAME production code path
the planner used (``resolve_batch_times`` / ``trading_day_offset`` /
``session_times`` against the frozen calendar build), not by re-implementing
the rules in this script.

Run inside a one-shot Core container on the core network (SELECT-only
role is sufficient), e.g. on sg-prod:

    set -a; . /opt/youwei/secrets/production.env; set +a
    docker run --rm --network youwei-production_core \
      -v /root/youwei-trading-agent/ops/verify_batch_preregistration.py:/harness/verify.py:ro \
      -e YOUWEI_DATABASE_URL=postgresql+asyncpg://youwei_app:$YOUWEI_POSTGRES_APP_PASSWORD@postgres:5432/youwei \
      ghcr.io/youweichen0208/youwei-core@sha256:54df4db578b05327dee233a962260599077a561a32a8ce9118b0086aa255dad4 \
      python /harness/verify.py

Exit code: 0 = all checks PASS, 1 = at least one FAIL (e.g. run before
the scheduler has preregistered batch 1 — that is a legitimate FAIL for
this script, which exists to confirm the preregistration).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid as uuid_mod
from datetime import datetime, timezone

from sqlalchemy import func, select

from youwei_core.config import Settings
from youwei_core.data.calendar import (
    resolve_batch_times,
    session_times,
    trading_day_offset,
)
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import campaigns, forecast_batches, forecast_cases, forecast_commits

CAMPAIGN_KEY = "phase1a-pilot-2026q4"
CAMPAIGN_ID = uuid_mod.UUID("a63f8494-4730-4cc7-bf2d-0b29e895aa3c")
TENANT_ID = uuid_mod.UUID("f497c122-45b6-497b-bb99-9c42401c3e5f")
BENCHMARK_SPY = uuid_mod.UUID("ee161699-08b6-4250-986a-b39a393a68df")
CUTOFF = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)  # batch 1, frozen plan
# S06j frozen 12-batch plan: Saturdays 06:00 ET; EDT through 10-31 (10:00Z),
# EST from 11-07 (11:00Z).
PLANNED_CUTOFFS = [
    datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 7, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 14, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 21, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 28, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 5, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 12, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 19, 11, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 26, 11, 0, tzinfo=timezone.utc),
]
HORIZONS = (1, 20, 60)
PANEL_SIZE = 20
CASES_PER_BATCH = PANEL_SIZE * len(HORIZONS)  # 60


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {detail}")
        if not ok:
            self.failures += 1


def _as_instant(value: object) -> datetime | None:
    """planned_cutoffs entries are ISO strings; parse to UTC instants."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=2, max_overflow=0)
    report = Report()

    try:
        async with engine.connect() as conn:
            campaign = (
                (
                    await conn.execute(
                        select(campaigns).where(campaigns.c.campaign_key == CAMPAIGN_KEY)
                    )
                )
                .mappings().one_or_none()
            )
            report.check(
                "campaign",
                campaign is not None,
                f"key={CAMPAIGN_KEY} "
                + ("found" if campaign is not None else "NOT FOUND"),
            )
            if campaign is None:
                print(f"\n{report.failures} failing check(s); cannot continue without the campaign")
                return 1

            report.check(
                "campaign identity",
                campaign.id == CAMPAIGN_ID and campaign.tenant_id == TENANT_ID,
                f"id={campaign.id} tenant={campaign.tenant_id} status={campaign.status}",
            )
            report.check("campaign active", campaign.status == "active", f"status={campaign.status}")
            report.check(
                "campaign benchmark",
                campaign.benchmark_security_id == BENCHMARK_SPY,
                f"benchmark={campaign.benchmark_security_id} (SPY expected)",
            )

            planned = [_as_instant(v) for v in (campaign.planned_cutoffs or [])]
            planned_ok = (
                len(planned) == len(PLANNED_CUTOFFS)
                and all(p is not None for p in planned)
                and [p for p in planned if p] == PLANNED_CUTOFFS
            )
            report.check(
                "frozen 12-cutoff plan intact",
                planned_ok,
                f"{len(planned)} planned cutoffs, first={planned[0] if planned else None}, "
                f"last={planned[-1] if planned else None}",
            )

            # --- check 1: exactly one registered batch for the campaign ---
            campaign_batches = (
                (
                    await conn.execute(
                        select(forecast_batches).where(
                            forecast_batches.c.campaign_id == campaign.id
                        )
                    )
                )
                .mappings().all()
            )
            total_batches = (
                await conn.execute(select(func.count()).select_from(forecast_batches))
            ).scalar_one()
            batch = campaign_batches[0] if campaign_batches else None
            report.check(
                "1: exactly one batch for the campaign",
                len(campaign_batches) == 1,
                f"campaign batches={len(campaign_batches)}, all-campaign batches={total_batches}",
            )
            if batch is None:
                print(
                    "\nBatch 1 is not registered yet (expected only after the "
                    "first scheduler tick past 2026-10-03 06:00 ET). "
                    f"{report.failures} failing check(s)."
                )
                return 1

            report.check(
                "1: batch cutoff",
                batch.decision_cutoff_utc == CUTOFF,
                f"cutoff={batch.decision_cutoff_utc.isoformat()} (expected {CUTOFF.isoformat()})",
            )
            report.check(
                "1: batch is a forward plan",
                batch.backfilled_plan is False,
                f"backfilled_plan={batch.backfilled_plan}",
            )

            # --- check 4 (duplicates): distinct keys == counts ---
            distinct_cutoffs = (
                await conn.execute(
                    select(func.count(func.distinct(forecast_batches.c.decision_cutoff_utc))).where(
                        forecast_batches.c.campaign_id == campaign.id
                    )
                )
            ).scalar_one()
            report.check(
                "4: no duplicate batches",
                distinct_cutoffs == len(campaign_batches),
                f"rows={len(campaign_batches)} distinct cutoffs={distinct_cutoffs}",
            )

            # --- check 2: exactly 60 cases ---
            case_rows = (
                (
                    await conn.execute(
                        select(forecast_cases).where(forecast_cases.c.batch_id == batch.id)
                    )
                )
                .mappings().all()
            )
            report.check(
                "2: batch carries exactly 60 cases",
                len(case_rows) == CASES_PER_BATCH,
                f"cases={len(case_rows)} (expected {CASES_PER_BATCH})",
            )

            distinct_keys = (
                await conn.execute(
                    select(
                        func.count(),
                    ).select_from(
                        select(
                            forecast_cases.c.batch_id,
                            forecast_cases.c.security_id,
                            forecast_cases.c.horizon_td,
                        )
                        .where(forecast_cases.c.batch_id == batch.id)
                        .distinct()
                        .subquery()
                    )
                )
            ).scalar_one()
            report.check(
                "4: no duplicate cases",
                distinct_keys == len(case_rows),
                f"rows={len(case_rows)} distinct (batch,security,horizon)={distinct_keys}",
            )

            securities = {str(r.security_id) for r in case_rows}
            panel = {str(x) for x in (campaign.panel_security_ids or [])}
            report.check(
                "2: 20 distinct panel securities",
                len(securities) == PANEL_SIZE and securities == panel,
                f"distinct={len(securities)}, matches campaign panel={securities == panel}",
            )

            by_horizon: dict[int, list] = {h: [] for h in HORIZONS}
            for r in case_rows:
                by_horizon.setdefault(r.horizon_td, []).append(r)
            horizon_ok = all(len(by_horizon.get(h, [])) == PANEL_SIZE for h in HORIZONS) and set(
                by_horizon
            ) == set(HORIZONS)
            report.check(
                "2: 20 cases per horizon {1,20,60}",
                horizon_ok,
                ", ".join(f"D{h}={len(by_horizon.get(h, []))}" for h in sorted(by_horizon)),
            )

            spec_by_horizon = {int(s["horizon_td"]): s for s in campaign.target_specs}
            specs_ok = all(
                all(
                    r.target_spec_id == spec_by_horizon[h]["target_spec_id"]
                    and r.target_spec_sha256 == spec_by_horizon[h]["content_sha256"]
                    for r in by_horizon.get(h, [])
                )
                for h in HORIZONS
                if h in spec_by_horizon
            ) and set(spec_by_horizon) == set(HORIZONS)
            report.check(
                "2: target specs match campaign registration",
                specs_ok,
                "; ".join(
                    f"D{h}->{spec_by_horizon[h]['target_spec_id']}" for h in HORIZONS if h in spec_by_horizon
                ),
            )

            benchmark_ok = all(r.benchmark_security_id == BENCHMARK_SPY for r in case_rows)
            report.check(
                "2: every case benchmarks against SPY",
                benchmark_ok,
                f"all={benchmark_ok}",
            )

            cutoff_ok = all(r.decision_cutoff_utc == CUTOFF for r in case_rows)
            report.check(
                "2: every case carries the batch cutoff",
                cutoff_ok,
                f"all={cutoff_ok}",
            )

            # --- check 3: windows match the frozen calendar (planner path) ---
            times = await resolve_batch_times(engine, CUTOFF)
            report.check(
                "3: batch cutoff/deadline/entry (resolve_batch_times)",
                batch.decision_cutoff_utc == times.decision_cutoff_utc
                and batch.prediction_deadline_utc == times.prediction_deadline_utc
                and batch.entry_date == times.entry_date
                and batch.entry_at_utc == times.entry_at_utc,
                f"entry={times.entry_date.isoformat()} {times.entry_at_utc.isoformat()}, "
                f"deadline={times.prediction_deadline_utc.isoformat()} "
                f"(calendar {times.calendar_version})",
            )

            for h in HORIZONS:
                exit_date = await trading_day_offset(engine, times.entry_date, h)
                exit_session = await session_times(engine, exit_date)
                rows = by_horizon.get(h, [])
                ok = all(r.exit_at_utc == exit_session.close_utc for r in rows)
                report.check(
                    f"3: D{h} exit windows (trading_day_offset/session_times)",
                    ok and len(rows) == PANEL_SIZE,
                    f"exit={exit_session.close_utc.isoformat()} "
                    f"({'early close' if exit_session.early_close else 'regular'}), rows={len(rows)}",
                )

            # --- sanity: no commits before the cutoff window ---
            commit_count = (
                await conn.execute(
                    select(func.count())
                    .select_from(forecast_commits)
                    .where(forecast_commits.c.case_id.in_([r.id for r in case_rows]))
                )
            ).scalar_one()
            report.check(
                "no commits before the cutoff window",
                commit_count == 0,
                f"commits={commit_count}",
            )
    finally:
        await engine.dispose()

    print(f"\n{'ALL CHECKS PASS' if report.failures == 0 else str(report.failures) + ' CHECK(S) FAILED'}")
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
