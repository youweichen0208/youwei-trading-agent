"""S04: Tiingo collector — rate-limited client, strict parsing,
immutable raw-object ingestion with content dedup, zero-volume
phantom-row flagging, and the data.tiingo_daily job handler.

Fixtures are abridged from real responses recorded 2026-09-27
(docs/research/tiingo-token-verification.md)."""

import asyncio
import json
import time
import uuid
from datetime import date

import httpx
import pytest

from youwei_core.data.securities import IdentitySpec, create_security
from youwei_core.data.tiingo import (
    TiingoClient,
    TiingoDailyPayload,
    ingest_daily,
    make_tiingo_daily_handler,
    parse_daily_rows,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, get_run_view, submit_run
from youwei_core.worker.loop import WorkerLoop

# --- fixtures (real shapes, abridged) ------------------------------------

AAPL_BODY = json.dumps(
    [
        {
            "date": "2026-08-07T00:00:00.000Z", "close": 313.33, "high": 314.0,
            "low": 311.51, "open": 313.16, "volume": 41450358,
            "adjClose": 313.055799436, "adjHigh": 313.72, "adjLow": 311.236,
            "adjOpen": 312.885, "adjVolume": 41450358, "divCash": 0.0,
            "splitFactor": 1.0,
        },
        {
            "date": "2026-08-10T00:00:00.000Z", "close": 308.26, "high": 308.26,
            "low": 304.61, "open": 306.83, "volume": 44812503,
            "adjClose": 308.26, "adjHigh": 308.26, "adjLow": 304.61,
            "adjOpen": 306.83, "adjVolume": 44812503, "divCash": 0.27,
            "splitFactor": 1.0,
        },
        {
            "date": "2020-08-31T00:00:00.000Z", "close": 129.04, "high": 130.0,
            "low": 124.0, "open": 127.0, "volume": 900000000,
            "adjClose": 125.0672, "adjHigh": 126.0, "adjLow": 120.0,
            "adjOpen": 122.9, "adjVolume": 900000000, "divCash": 0.0,
            "splitFactor": 4.0,
        },
    ]
)

# SGEN phantom rows: frozen at the acquisition price, volume 0,
# emitted on trading days after delisting
SGEN_BODY = json.dumps(
    [
        {
            "date": "2023-12-13T00:00:00.000Z", "close": 228.74, "high": 229.0,
            "low": 226.5, "open": 227.0, "volume": 20075430,
            "adjClose": 228.74, "adjHigh": 229.0, "adjLow": 226.5,
            "adjOpen": 227.0, "adjVolume": 20075430, "divCash": 0.0,
            "splitFactor": 1.0,
        },
        {
            "date": "2023-12-14T00:00:00.000Z", "close": 228.74, "high": 228.74,
            "low": 228.74, "open": 228.74, "volume": 0,
            "adjClose": 228.74, "adjHigh": 228.74, "adjLow": 228.74,
            "adjOpen": 228.74, "adjVolume": 0, "divCash": 0.0,
            "splitFactor": 1.0,
        },
        {
            "date": "2023-12-15T00:00:00.000Z", "close": 228.74, "high": 228.74,
            "low": 228.74, "open": 228.74, "volume": 0,
            "adjClose": 228.74, "adjHigh": 228.74, "adjLow": 228.74,
            "adjOpen": 228.74, "adjVolume": 0, "divCash": 0.0,
            "splitFactor": 1.0,
        },
    ]
)


def _client(bodies: dict, *, status: int = 200, min_interval: float = 0.0) -> TiingoClient:
    def handler(request: httpx.Request) -> httpx.Response:
        ticker = request.url.path.rsplit("/", 2)[-2]
        if ticker in bodies:
            return httpx.Response(status, text=bodies[ticker])
        return httpx.Response(404, text='{"detail":"Not found."}')

    return TiingoClient(
        "test-token", base_url="https://mock.test/tiingo",
        min_interval=min_interval, transport=httpx.MockTransport(handler),
    )


async def _security(engine, ticker="AAPL"):
    return await create_security(
        engine, identities=[IdentitySpec("ticker", ticker, date(1990, 1, 1))]
    )


# --- client -----------------------------------------------------------------


async def test_client_rate_limits_min_interval():
    client = _client({"AAPL": AAPL_BODY}, min_interval=0.15)
    t0 = time.monotonic()
    await client.daily_prices("AAPL", date(2026, 8, 1), date(2026, 8, 31))
    await client.daily_prices("AAPL", date(2026, 8, 1), date(2026, 8, 31))
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.15  # second call waited for the interval
    await client.aclose()


async def test_client_passes_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen.update(request.url.params)
        return httpx.Response(200, text="[]")

    client = TiingoClient(
        "tok", base_url="https://mock.test/tiingo",
        transport=httpx.MockTransport(handler),
    )
    await client.daily_prices("aapl", date(2026, 8, 1), date(2026, 8, 31))
    await client.aclose()
    assert seen["token"] == "tok"
    assert seen["startDate"] == "2026-08-01"
    assert seen["endDate"] == "2026-08-31"
    assert seen["path"].endswith("/daily/AAPL/prices")  # ticker uppercased


async def test_client_raises_on_http_error():
    client = _client({"AAPL": "nope"}, status=403)
    from youwei_core.data.tiingo import TiingoError

    with pytest.raises(TiingoError, match="403"):
        await client.daily_prices("AAPL", date(2026, 8, 1), date(2026, 8, 31))
    await client.aclose()


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"detail": "Not found."}',          # object, not array
        '[{"close": 1.0}]',                   # missing fields
        '[{"date": "not-a-date", "open": 1, "high": 1, "low": 1, "close": 1,'
        ' "volume": 1, "divCash": 0, "splitFactor": 1}]',
        '["not an object"]',
    ],
)
def test_parse_rejects_structurally_invalid_bodies(body):
    with pytest.raises(ValueError):
        parse_daily_rows(body)


def test_parse_normalizes_fields():
    rows = parse_daily_rows(AAPL_BODY)
    assert len(rows) == 3
    by_date = {r["trade_date"].isoformat(): r for r in rows}
    div_row = by_date["2026-08-10"]
    assert float(div_row["div_cash"]) == 0.27
    assert float(div_row["close"]) == 308.26
    split_row = by_date["2020-08-31"]
    assert float(split_row["split_factor"]) == 4.0


# --- ingestion -----------------------------------------------------------------


async def test_ingest_stores_raw_object_and_observations(db_engine):
    sec = await _security(db_engine)
    client = _client({"AAPL": AAPL_BODY})
    result = await ingest_daily(
        db_engine, client,
        security_id=sec, ticker="AAPL",
        start_date=date(2020, 8, 1), end_date=date(2026, 8, 31),
    )
    assert result.created is True
    assert result.rows == 3

    from sqlalchemy import select

    from youwei_core.db.meta import raw_objects, price_observations, data_sources

    async with db_engine.begin() as conn:
        raw = (
            await conn.execute(select(raw_objects).where(raw_objects.c.id == result.raw_object_id))
        ).mappings().one()
        obs = (
            await conn.execute(
                select(price_observations)
                .where(price_observations.c.security_id == sec)
                .order_by(price_observations.c.trade_date)
            )
        ).mappings().all()
        src = (
            await conn.execute(select(data_sources).where(data_sources.c.id == "tiingo"))
        ).mappings().one()

    # raw evidence: exact content, hashes, conservative timestamps
    assert raw["content"] == AAPL_BODY
    assert raw["content_sha256"] == __import__("hashlib").sha256(
        AAPL_BODY.encode()
    ).hexdigest()
    assert raw["source_available_at"] is None
    assert raw["source_available_basis"] == "not_exposed_by_vendor"
    assert raw["usable_at"] == raw["ingested_at"]  # no evidence: conservative
    assert raw["parser_version"] == "tiingo-daily-v1"
    assert raw["row_count"] == 3
    assert src["license_tags"] == ["market-data", "vendor:tiingo"]

    # observations: parsed values preserved exactly
    assert [o["trade_date"].isoformat() for o in obs] == [
        "2020-08-31", "2026-08-07", "2026-08-10",
    ]
    assert all(o["quality"] == "ok" for o in obs)
    assert float(obs[2]["div_cash"]) == 0.27
    assert float(obs[0]["split_factor"]) == 4.0
    assert str(obs[0]["close"]) == "129.04000000"


async def test_ingest_flags_zero_volume_phantom_rows(db_engine):
    sec = await _security(db_engine, ticker="SGEN")
    client = _client({"SGEN": SGEN_BODY})
    result = await ingest_daily(
        db_engine, client,
        security_id=sec, ticker="SGEN",
        start_date=date(2023, 12, 1), end_date=date(2023, 12, 31),
    )
    assert result.rows == 3

    from sqlalchemy import select

    from youwei_core.db.meta import price_observations

    async with db_engine.begin() as conn:
        obs = (
            await conn.execute(
                select(price_observations)
                .where(price_observations.c.security_id == sec)
                .order_by(price_observations.c.trade_date)
            )
        ).mappings().all()
    assert [o["quality"] for o in obs] == ["ok", "zero_volume", "zero_volume"]
    assert obs[1]["close"] == obs[2]["close"] == obs[0]["close"]  # frozen price preserved as-is


async def test_ingest_identical_content_dedupes(db_engine):
    sec = await _security(db_engine)
    client = _client({"AAPL": AAPL_BODY})
    kwargs = dict(
        security_id=sec, ticker="AAPL",
        start_date=date(2020, 8, 1), end_date=date(2026, 8, 31),
    )
    r1 = await ingest_daily(db_engine, client, **kwargs)
    r2 = await ingest_daily(db_engine, client, **kwargs)  # job retry path
    assert r1.created is True
    assert r2.created is False
    assert r2.raw_object_id == r1.raw_object_id

    from sqlalchemy import func, select

    from youwei_core.db.meta import price_observations, raw_objects

    async with db_engine.begin() as conn:
        n_raw = (await conn.execute(select(func.count()).select_from(raw_objects))).scalar_one()
        n_obs = (
            await conn.execute(select(func.count()).select_from(price_observations))
        ).scalar_one()
    assert n_raw == 1
    assert n_obs == 3


async def test_ingest_source_available_evidence_recorded(db_engine):
    from datetime import datetime, timezone

    sec = await _security(db_engine)
    client = _client({"AAPL": AAPL_BODY})
    evidence = datetime(2026, 8, 10, 2, 15, 41, tzinfo=timezone.utc)
    result = await ingest_daily(
        db_engine, client,
        security_id=sec, ticker="AAPL",
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
        source_available_at=evidence,
        source_available_basis="vendor_fundamentals_meta.dailyLastUpdated",
    )
    from sqlalchemy import select

    from youwei_core.db.meta import raw_objects

    async with db_engine.begin() as conn:
        raw = (
            await conn.execute(select(raw_objects).where(raw_objects.c.id == result.raw_object_id))
        ).mappings().one()
    assert raw["source_available_at"] == evidence
    assert raw["source_available_basis"] == "vendor_fundamentals_meta.dailyLastUpdated"
    # usable_at never pretends data was usable before it actually arrived
    assert raw["usable_at"] >= raw["ingested_at"]


# --- job handler ---------------------------------------------------------------


async def test_tiingo_daily_job_handler(db_engine, tenant_id):
    sec = await _security(db_engine)
    client = _client({"AAPL": AAPL_BODY})
    handler = make_tiingo_daily_handler(db_engine, client)

    submission = RunSubmission(
        kind="data.ingest",
        jobs=[
            JobSubmission(
                kind="data.tiingo_daily",
                payload={
                    "ticker": "AAPL",
                    "security_id": str(sec),
                    "start_date": "2020-08-01",
                    "end_date": "2026-08-31",
                },
            )
        ],
    )
    run_id = (
        await submit_run(db_engine, tenant_id, submission, f"ti-{uuid.uuid4().hex[:8]}")
    ).run_id

    loop = WorkerLoop(db_engine, handlers={"data.tiingo_daily": handler})
    assert await loop.run_once() is True
    assert await loop.run_once() is False  # queue drained

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "succeeded"

    # the attempt result carries the ingest outcome (audit trail)
    from sqlalchemy import select

    from youwei_core.db.meta import attempts

    async with db_engine.begin() as conn:
        result = (
            await conn.execute(select(attempts.c.result))
        ).scalar_one()
    assert result["rows"] == 3
    assert result["created"] is True
    assert "raw_object_id" in result

    # payload validation is enforced by the handler
    with pytest.raises(Exception):
        TiingoDailyPayload.model_validate({"ticker": ""})
    await client.aclose()
