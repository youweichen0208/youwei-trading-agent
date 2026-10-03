"""Seed the S12 rollout-rehearsal database (throwaway, fully isolated).

Runs INSIDE a one-shot core image container on the rehearsal core network
(mounted read-only; see ops/s12_rollout_rehearsal.sh). Builds exactly what
the exploratory research flow borrows, through the REAL service paths:

- the versioned calendar (build_calendar),
- a tenant + API key (auth service),
- a seeded security (ticker REH1, rising closes) and SPY (flat) with ~30
  sessions of synthetic bars ingested through the real Tiingo ingest path
  (immutable raw objects + PIT observations),
- an ACTIVE campaign registered through the real release -> owner approval
  (scope manifest) -> campaign flow (make_campaign_plan), so submit-side
  borrowing of target specs/benchmark works exactly as in production.

Prints ONE JSON line on stdout with the created identities (the driver
consumes it). Nothing here touches the production database.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import text

from youwei_core.config import Settings
from youwei_core.data.calendar import build_calendar, next_weekly_cutoff
from youwei_core.data.securities import IdentitySpec, create_security
from youwei_core.data.tiingo import TiingoClient, ingest_daily
from youwei_core.db.engine import make_engine
from youwei_core.ledger.plan import CampaignPlanScope, prepare_campaign_plan
from youwei_core.ledger.service import (
    approve_release,
    register_campaign,
    register_release,
)
from youwei_core.auth.service import create_api_key, create_tenant

TENANT_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
TICKER = "REH1"
BENCHMARK = "SPY"
SPEC_SHA = "e" * 64
TIME_SHA = "f" * 64
PANEL_N = 3  # REH1 + 2 fillers so the panel shape is realistic


def _bar(d: date, open_: float, close: float) -> dict:
    return {
        "date": f"{d.isoformat()}T00:00:00.000Z",
        "open": open_, "high": max(open_, close) + 1.0, "low": min(open_, close) - 1.0,
        "close": close, "volume": 1000,
        "adjClose": close, "adjHigh": close + 1.0, "adjLow": close - 1.0,
        "adjOpen": open_, "adjVolume": 1000,
        "divCash": 0.0, "splitFactor": 1.0,
    }


def _specs():
    return [
        {"horizon_td": h, "target_spec_id": f"excess-tr-d{h}-v1", "content_sha256": SPEC_SHA}
        for h in (1, 20, 60)
    ]


async def main() -> int:
    engine = make_engine(Settings().database_url, pool_size=2)
    try:
        now = datetime.now(UTC)
        await build_calendar(engine, year_start=2024, year_end=now.year + 2)

        # trading days: last 30 completed sessions before today
        async with engine.connect() as conn:
            dates = list(
                (
                    await conn.execute(
                        text(
                            "SELECT date FROM calendar_days WHERE venue='XNYS' "
                            "AND is_trading AND date < :today ORDER BY date DESC LIMIT 30"
                        ),
                        {"today": now.date()},
                    )
                ).scalars().all()
            )
        dates = sorted(dates)
        assert len(dates) == 30

        await create_tenant(engine, "s12-rehearsal", tenant_id=TENANT_ID)
        _, api_key = await create_api_key(engine, TENANT_ID, "rehearsal")

        securities = {}
        for i in range(PANEL_N):
            ticker = TICKER if i == 0 else f"RBF{i}"
            securities[ticker] = await create_security(
                engine, identities=[IdentitySpec("ticker", ticker, date(1990, 1, 1))]
            )
        benchmark_id = await create_security(
            engine, identities=[IdentitySpec("ticker", BENCHMARK, date(1990, 1, 1))]
        )

        # bars through the REAL ingest path (MockTransport serving the bodies)
        bodies = {}
        for i, ticker in enumerate(securities):
            rows = [
                _bar(d, 100.0 + i, 100.0 + i + j * 1.5)  # rising closes (momentum > 0)
                for j, d in enumerate(dates)
            ]
            bodies[ticker] = json.dumps(rows)
        bodies[BENCHMARK] = json.dumps([_bar(d, 500.0, 500.0) for d in dates])

        def handler(request: httpx.Request) -> httpx.Response:
            # the client requests {base}/daily/{TICKER}/prices — the ticker
            # is the SECOND-TO-LAST path segment (the last is "prices").
            # (Regression 2026-10-03: extracting the last segment returned
            # "prices" for every request, so every ingest silently received
            # an empty body and the research snapshot froze ZERO bars.)
            segments = request.url.path.rstrip("/").split("/")
            ticker = segments[-2] if segments[-1] == "prices" else segments[-1]
            return httpx.Response(200, content=bodies.get(ticker, "[]").encode())

        client = TiingoClient(
            "rehearsal-token", base_url="https://rehearsal.invalid/tiingo",
            transport=httpx.MockTransport(handler),
        )
        for ticker, sec_id in securities.items():
            await ingest_daily(
                engine, client, security_id=sec_id, ticker=ticker,
                start_date=dates[0], end_date=dates[-1],
            )
        await ingest_daily(
            engine, client, security_id=benchmark_id, ticker=BENCHMARK,
            start_date=dates[0], end_date=dates[-1],
        )

        # fail fast on a silent empty ingest (the mock handler returning []
        # for every ticker previously froze a zero-bar snapshot and the
        # defect only surfaced as the model's honest "zero rows" answer)
        async with engine.begin() as conn:
            bar_count = (
                await conn.execute(
                    text("SELECT count(*) FROM price_observations")
                )
            ).scalar_one()
        expected = 30 * (len(securities) + 1)
        assert bar_count == expected, (
            f"ingest produced {bar_count} bars, expected {expected} — "
            "the mock Tiingo responses were probably not matched"
        )

        # release -> owner approval (scope) -> active campaign
        release_id = "rel-s12-rehearsal-v1"
        await register_release(engine, release_id=release_id, manifest={
            "enabled_sources": ["baseline", "quant_model"],
            "fallback_policy": "phase1a-none",
            "prompt_version": "not_enabled",
        })
        async with engine.begin() as conn:
            release_sha = (
                await conn.execute(
                    text(
                        "SELECT release_content_sha256 FROM research_releases "
                        "WHERE release_id = :rid"
                    ),
                    {"rid": release_id},
                )
            ).scalar_one()
        plan = prepare_campaign_plan(
            tenant_id=TENANT_ID,
            campaign_key="s12-rehearsal",
            release_content_sha256=release_sha,
            phase="1a",
            first_cutoff=next_weekly_cutoff(now),
            batch_count=4,
            panel_security_ids=[str(s) for s in securities.values()],
            benchmark_security_id=str(benchmark_id),
            target_specs=_specs(),
            enabled_sources=["baseline", "quant_model"],
            fallback_policy="phase1a-none",
            primary_metric="d20_paired_brier_delta",
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
        )
        scope = CampaignPlanScope(
            phase="1a",
            tenant_id=TENANT_ID,
            campaign_key="s12-rehearsal",
            release_content_sha256=release_sha,
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )
        await approve_release(
            engine, release_id=release_id, approver_principal_id="human-owner",
            scope="s12-rehearsal", scope_manifest=scope.model_dump(mode="json"),
        )
        campaign = await register_campaign(
            engine,
            tenant_id=TENANT_ID,
            campaign_key="s12-rehearsal",
            release_id=release_id,
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark_id,
            panel_security_ids=[str(s) for s in securities.values()],
            panel_manifest={"sampler_version": "sector-stratified-hash-v1"},
            enabled_sources=["baseline", "quant_model"],
            planned_cutoffs=plan["planned_cutoffs"],
            planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
            campaign_plan_sha256=plan["campaign_plan_sha256"],
        )
        print(json.dumps({
            "tenant_id": str(TENANT_ID),
            "api_key": api_key,
            "ticker": TICKER,
            "security_id": str(securities[TICKER]),
            "benchmark_security_id": str(benchmark_id),
            "campaign_id": str(campaign.campaign_id),
            "window": [dates[0].isoformat(), dates[-1].isoformat()],
        }))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
