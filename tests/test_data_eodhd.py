"""EODHD constituents collector (S06): strict parsing + immutable
raw-object ingestion, mirroring the Tiingo collector discipline."""

import json
from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import text

from youwei_core.data.eodhd import (
    EODHDClient,
    EODHDError,
    ingest_sp500_constituents,
    parse_constituents,
)

BODY = json.dumps(
    {
        "General": {
            "Code": "GSPC",
            "Name": "S&P 500 Index",
            "Exchange": "INDX",
            "MarketCap": 66722401246569,
        },
        "Components": {
            "0": {"Code": "AAPL", "Exchange": "US", "Name": "Apple", "Sector": "Technology", "Industry": "Consumer Electronics", "Weight": 0.07},
            "1": {"Code": "BRK-B", "Exchange": "US", "Name": "Berkshire", "Sector": "Financial Services", "Industry": "Insurance", "Weight": 0.02},
            "2": {"Code": "JPM", "Exchange": "US", "Name": "JPMorgan", "Sector": "Financial Services", "Industry": "Banks", "Weight": 0.03},
        },
        "HistoricalTickerComponents": {
            "0": {"Code": "SIVB", "Name": "SVB", "StartDate": "2018-03-19", "EndDate": "2023-03-15", "IsActiveNow": 0, "IsDelisted": 1},
        },
    }
)


def _client(body: str = BODY, *, status: int = 200, min_interval: float = 0.0) -> EODHDClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return EODHDClient(
        "test-token",
        base_url="https://mock.test/api",
        min_interval=min_interval,
        transport=httpx.MockTransport(handler),
    )


# --- parsing ------------------------------------------------------------------


def test_parse_constituents_normalizes():
    parsed = parse_constituents(BODY)
    assert parsed["general"]["index"] == "GSPC"
    assert [c["ticker"] for c in parsed["components"]] == ["AAPL", "BRK-B", "JPM"]
    assert parsed["components"][0]["sector"] == "Technology"
    assert str(parsed["components"][0]["weight"]) == "0.07"
    assert parsed["historical"][0]["ticker"] == "SIVB"
    assert parsed["historical"][0]["start_date"] == date(2018, 3, 19)
    assert parsed["historical"][0]["end_date"] == date(2023, 3, 15)
    assert parsed["historical"][0]["is_delisted"] is True


def test_parse_rejects_junk():
    with pytest.raises(ValueError, match="not JSON"):
        parse_constituents("not json")
    with pytest.raises(ValueError, match="General"):
        parse_constituents(json.dumps({"Components": {"0": {}}}))
    with pytest.raises(ValueError, match="Components"):
        parse_constituents(json.dumps({"General": {"Code": "GSPC"}}))
    with pytest.raises(ValueError, match="Sector"):
        parse_constituents(
            json.dumps(
                {
                    "General": {"Code": "GSPC"},
                    "Components": {"0": {"Code": "AAPL", "Exchange": "US"}},
                }
            )
        )


# --- ingestion -----------------------------------------------------------------


async def test_ingest_stores_immutable_raw_object_and_dedups(db_engine):
    client = _client()
    first = await ingest_sp500_constituents(db_engine, client)
    assert first.created and len(first.components) == 3

    second = await ingest_sp500_constituents(db_engine, _client())
    assert not second.created
    assert second.raw_object_id == first.raw_object_id

    async with db_engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM raw_objects"))).scalar_one()
    assert n == 1  # identical content never stored twice
    await client.aclose()


async def test_ingest_records_missing_source_time_basis(db_engine):
    client = _client()
    result = await ingest_sp500_constituents(db_engine, client)
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT source_available_basis, usable_at IS NOT NULL AS usable "
                    "FROM raw_objects WHERE id = :i"
                ),
                {"i": str(result.raw_object_id)},
            )
        ).mappings().one()
    assert row["usable"] is True
    assert row["source_available_basis"] == "vendor_does_not_expose_source_time"
    await client.aclose()


async def test_ingest_records_caller_evidence_when_source_time_given(db_engine):
    """A caller-supplied source time is recorded with the
    caller_evidence basis (the bug this locks in: an absent basis used
    to violate the NOT NULL column)."""
    client = _client()
    stamp = datetime(2026, 9, 28, 6, 30, 0, tzinfo=timezone.utc)
    result = await ingest_sp500_constituents(
        db_engine, client, source_available_at=stamp
    )
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT source_available_basis, source_available_at "
                    "FROM raw_objects WHERE id = :i"
                ),
                {"i": str(result.raw_object_id)},
            )
        ).mappings().one()
    assert row["source_available_basis"] == "caller_evidence"
    assert row["source_available_at"] == stamp
    await client.aclose()


async def test_client_errors_on_non_200():
    client = _client(status=403)
    with pytest.raises(EODHDError, match="403"):
        await client.constituents("GSPC")
    await client.aclose()
