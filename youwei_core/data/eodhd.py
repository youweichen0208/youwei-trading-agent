"""EODHD S&P 500 constituents collector (S06, campaign-policy §2.1).

The Indices Historical Constituents Marketplace product lives in the
/api/mp/ namespace (NOT the standard /api/fundamentals/), verified
with a real token 2026-09-28 (see
docs/research/eodhd-constituents-verification.md)::

    /api/mp/unicornbay/spglobal/comp/{INDEX}.INDX

The response carries the current components plus historical membership
(StartDate/EndDate/IsActiveNow/IsDelisted). This module fetches the
response, validates it strictly, and stores it as an immutable
content-hashed raw object — the same evidence discipline as the Tiingo
collector: junk is never partially ingested, and identical content
dedups to the same raw object.

The constituent members are turned into the sampling frame by
youwei_core.data.panel (ticker -> permanent security_id + sector).
"""

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import data_sources, raw_objects

# fixed identity for this vendor + endpoint (stable across deploys)
SOURCE_ID = "eodhd"
ENDPOINT = "mp/unicornbay/spglobal/comp"
PARSER_VERSION = "spglobal-constituents-v1"
NO_SOURCE_TIME_BASIS = "vendor_does_not_expose_source_time"


class EODHDError(Exception):
    pass


class EODHDClient:
    """Minimal rate-limited client for the constituents endpoint.
    Tests inject an httpx transport instead of the network."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://eodhd.com/api",
        min_interval: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def constituents(self, index: str = "GSPC") -> str:
        """Return the raw response body for the index's constituents."""
        if not self.token:
            raise EODHDError("eodhd token not configured")
        await self._throttle()
        response = await self._http.get(
            f"{self.base_url}/{ENDPOINT}/{index.upper()}.INDX",
            params={"api_token": self.token, "fmt": "json"},
        )
        if response.status_code != 200:
            raise EODHDError(
                f"eodhd constituents {index} HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        return response.text


# --- parsing ----------------------------------------------------------------


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def parse_constituents(content: str) -> dict:
    """Validate and normalize a constituents response. Raises
    ValueError on anything structurally unexpected — junk is never
    partially ingested."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    general = data.get("General")
    if not isinstance(general, dict) or not general.get("Code"):
        raise ValueError("General section missing or lacks an index Code")

    components_raw = data.get("Components")
    if not isinstance(components_raw, dict) or not components_raw:
        raise ValueError("Components section missing or empty")

    components = []
    for key in sorted(components_raw, key=int):
        row = components_raw[key]
        if not isinstance(row, dict):
            raise ValueError(f"component {key} is not an object")
        missing = [f for f in ("Code", "Sector") if f not in row or not row[f]]
        if missing:
            raise ValueError(f"component {key} missing {missing}")
        components.append(
            {
                "ticker": str(row["Code"]),
                "exchange": str(row.get("Exchange", "US")),
                "name": row.get("Name"),
                "sector": str(row["Sector"]),
                "industry": row.get("Industry"),
                "weight": _dec(row.get("Weight")),
            }
        )

    historical = []
    hist_raw = data.get("HistoricalTickerComponents")
    if isinstance(hist_raw, dict):
        for key in sorted(hist_raw, key=int):
            row = hist_raw[key]
            if not isinstance(row, dict) or not row.get("Code"):
                continue  # tolerate gaps in the optional historical section
            historical.append(
                {
                    "ticker": str(row["Code"]),
                    "name": row.get("Name"),
                    "start_date": _parse_date(row.get("StartDate")),
                    "end_date": _parse_date(row.get("EndDate")),
                    "is_active_now": bool(row.get("IsActiveNow")),
                    "is_delisted": bool(row.get("IsDelisted")),
                }
            )

    return {
        "general": {
            "index": str(general["Code"]),
            "name": general.get("Name"),
            "exchange": general.get("Exchange"),
        },
        "components": components,
        "historical": historical,
    }


# --- ingestion ----------------------------------------------------------------


@dataclass
class ConstituentIngest:
    raw_object_id: uuid.UUID
    usable_at: datetime
    created: bool
    general: dict
    components: list
    historical: list


def _canonical_query(index: str) -> dict:
    return {"index": index.upper()}


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _ensure_source(conn, client: EODHDClient) -> None:
    await conn.execute(
        pg_insert(data_sources)
        .values(
            id=SOURCE_ID,
            name="EODHD",
            vendor_version="spglobal-constituents",
            license_tags=["market-data", "vendor:eodhd", "index-constituents"],
            config={"base_url": client.base_url},
        )
        .on_conflict_do_nothing(index_elements=[data_sources.c.id])
    )


async def ingest_sp500_constituents(
    engine: AsyncEngine,
    client: EODHDClient,
    *,
    index: str = "GSPC",
    source_available_at: datetime | None = None,
    source_available_basis: str | None = None,
) -> ConstituentIngest:
    """Fetch, validate and store the index's constituents as an
    immutable raw object. Identical content dedups to the same object.

    The response carries no source timestamp; when the caller cannot
    supply evidence-backed source_available_at, usable_at remains the
    conservative availability time and the basis is recorded."""
    body = await client.constituents(index)
    parsed = parse_constituents(body)

    query = _canonical_query(index)
    query_json = json.dumps(query, sort_keys=True, separators=(",", ":"))
    query_sha = _sha256_hex(query_json)
    content_sha = _sha256_hex(body)

    if source_available_at is not None:
        basis = source_available_basis or "caller_evidence"
    else:
        basis = source_available_basis or NO_SOURCE_TIME_BASIS

    async with engine.begin() as conn:
        await _ensure_source(conn, client)
        existing = (
            await conn.execute(
                select(raw_objects).where(
                    raw_objects.c.source_id == SOURCE_ID,
                    raw_objects.c.endpoint == ENDPOINT,
                    raw_objects.c.query_sha256 == query_sha,
                    raw_objects.c.content_sha256 == content_sha,
                )
            )
        ).mappings().first()
        if existing is not None:
            return ConstituentIngest(
                raw_object_id=existing.id,
                usable_at=existing.usable_at,
                created=False,
                general=parsed["general"],
                components=parsed["components"],
                historical=parsed["historical"],
            )

        raw_id = uuid.uuid4()
        now = (await conn.execute(select(func.now()))).scalar_one()
        await conn.execute(
            raw_objects.insert().values(
                id=raw_id,
                source_id=SOURCE_ID,
                endpoint=ENDPOINT,
                query=query,
                query_sha256=query_sha,
                content=body,
                content_sha256=content_sha,
                content_type="application/json",
                row_count=len(parsed["components"]),
                parser_version=PARSER_VERSION,
                source_available_at=source_available_at,
                source_available_basis=basis,
                ingested_at=now,
                usable_at=now,
            )
        )
    return ConstituentIngest(
        raw_object_id=raw_id,
        usable_at=now,
        created=True,
        general=parsed["general"],
        components=parsed["components"],
        historical=parsed["historical"],
    )
