"""Tiingo EOD collector: rate-limited client, daily-bar ingestion and
the `data.tiingo_daily` job handler.

Field semantics verified 2026-09-27 (docs/research/tiingo-token-
verification.md):
- raw OHLCV + divCash + splitFactor are the authoritative inputs for
  target-spec total returns; adjClose is cross-check only (measured
  ~1e-5 vendor adjustment drift)
- volume==0 rows are flagged quality='zero_volume': Tiingo emits
  phantom rows for some delisted securities (SGEN: close frozen at the
  acquisition price, endDate misleadingly fresh)
- the daily endpoint exposes no source timestamp, so
  source_available_at stays NULL with a recorded basis unless the
  caller supplies evidence; usable_at (>= ingested_at) is then the
  conservative availability time — never pretend history existed

Dedup semantics: identical bytes for the same query are one raw
object; a vendor correction is new content and therefore a new PIT
version. Job retries that re-fetch unchanged data do not create
versions.
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
from pydantic import UUID4, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import data_sources, price_observations, raw_objects
from youwei_core.jobs.worker import ClaimedJob

PARSER_VERSION = "tiingo-daily-v1"
SOURCE_ID = "tiingo"
ENDPOINT = "daily_prices"

NO_SOURCE_TIME_BASIS = "not_exposed_by_vendor"

_REQUIRED_FIELDS = (
    "date", "open", "high", "low", "close", "volume",
    "divCash", "splitFactor",
)


class TiingoError(Exception):
    pass


class TiingoClient:
    """Minimal rate-limited client for the daily prices endpoint.

    The limiter is a fixed minimum interval between calls (the
    evaluation tier's exact quota is not exposed via headers); tests
    inject an httpx transport instead of the network."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.tiingo.com/tiingo",
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

    async def daily_prices(
        self, ticker: str, start_date: date, end_date: date
    ) -> str:
        """Return the raw response body text for /daily/{ticker}/prices."""
        if not self.token:
            raise TiingoError("tiingo token not configured")
        await self._throttle()
        response = await self._http.get(
            f"{self.base_url}/daily/{ticker.upper()}/prices",
            params={
                "token": self.token,
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        if response.status_code != 200:
            raise TiingoError(
                f"tiingo daily/{ticker} HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        return response.text


@dataclass
class DailyIngest:
    raw_object_id: uuid.UUID
    rows: int
    usable_at: datetime
    created: bool  # False when identical content was already ingested


def _dec(value) -> Decimal:
    # str() round-trip keeps the vendor's decimal literal; floats
    # would smuggle binary artifacts into Numeric columns
    return Decimal(str(value))


def parse_daily_rows(content: str) -> list[dict]:
    """Validate and normalize a daily-prices response body. Raises
    ValueError on anything structurally unexpected — junk is never
    partially ingested."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response is not JSON: {exc}") from None
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array, got {type(data).__name__}")

    rows = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"row {i} is not an object")
        missing = [f for f in _REQUIRED_FIELDS if f not in row]
        if missing:
            raise ValueError(f"row {i} missing fields: {missing}")
        try:
            trade_date = date.fromisoformat(str(row["date"])[:10])
        except ValueError:
            raise ValueError(f"row {i} has unparseable date {row['date']!r}") from None
        rows.append(
            {
                "trade_date": trade_date,
                "open": _dec(row["open"]),
                "high": _dec(row["high"]),
                "low": _dec(row["low"]),
                "close": _dec(row["close"]),
                "volume": int(row["volume"]),
                "adj_close": _dec(row["adjClose"]) if "adjClose" in row else None,
                "div_cash": _dec(row["divCash"]),
                "split_factor": _dec(row["splitFactor"]),
            }
        )
    return rows


def _canonical_query(ticker: str, start_date: date, end_date: date) -> dict:
    return {
        "ticker": ticker.upper(),
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _ensure_source(conn, client: TiingoClient) -> None:
    await conn.execute(
        pg_insert(data_sources)
        .values(
            id=SOURCE_ID,
            name="Tiingo",
            vendor_version="eod-api",
            license_tags=["market-data", "vendor:tiingo"],
            config={"base_url": client.base_url},
        )
        .on_conflict_do_nothing(index_elements=[data_sources.c.id])
    )


def _find_existing(conn, query_sha: str, content_sha: str):
    return conn.execute(
        select(raw_objects).where(
            raw_objects.c.source_id == SOURCE_ID,
            raw_objects.c.endpoint == ENDPOINT,
            raw_objects.c.query_sha256 == query_sha,
            raw_objects.c.content_sha256 == content_sha,
        )
    )


async def ingest_daily(
    engine: AsyncEngine,
    client: TiingoClient,
    *,
    security_id: uuid.UUID,
    ticker: str,
    start_date: date,
    end_date: date,
    source_available_at: datetime | None = None,
    source_available_basis: str | None = None,
) -> DailyIngest:
    """Fetch, validate and store one daily-prices response as an
    immutable raw object plus its parsed observations.

    source_available_at: evidence-backed external availability time
    (e.g. a vendor metadata timestamp fetched alongside). When absent
    the object records why (vendor does not expose one) and usable_at
    remains the conservative availability time.
    """
    body = await client.daily_prices(ticker, start_date, end_date)
    rows = parse_daily_rows(body)

    query = _canonical_query(ticker, start_date, end_date)
    query_json = json.dumps(query, sort_keys=True, separators=(",", ":"))
    query_sha = _sha256_hex(query_json)
    content_sha = _sha256_hex(body)

    if source_available_at is not None:
        basis = source_available_basis or "caller_evidence"
    else:
        basis = source_available_basis or NO_SOURCE_TIME_BASIS

    try:
        async with engine.begin() as conn:
            await _ensure_source(conn, client)

            existing = (
                await _find_existing(conn, query_sha, content_sha)
            ).mappings().first()
            if existing is not None:
                return DailyIngest(
                    raw_object_id=existing.id,
                    rows=existing.row_count,
                    usable_at=existing.usable_at,
                    created=False,
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
                    row_count=len(rows),
                    parser_version=PARSER_VERSION,
                    source_available_at=source_available_at,
                    source_available_basis=basis,
                    ingested_at=now,
                    usable_at=now,
                )
            )
            for row in rows:
                await conn.execute(
                    price_observations.insert().values(
                        id=uuid.uuid4(),
                        raw_object_id=raw_id,
                        source_id=SOURCE_ID,
                        security_id=security_id,
                        trade_date=row["trade_date"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                        adj_close=row["adj_close"],
                        div_cash=row["div_cash"],
                        split_factor=row["split_factor"],
                        quality="zero_volume" if row["volume"] == 0 else "ok",
                    )
                )
        return DailyIngest(
            raw_object_id=raw_id, rows=len(rows), usable_at=now, created=True
        )
    except IntegrityError:
        # concurrent ingest of identical content: the loser re-reads
        async with engine.begin() as conn:
            existing = (
                await _find_existing(conn, query_sha, content_sha)
            ).mappings().one()
        return DailyIngest(
            raw_object_id=existing.id,
            rows=existing.row_count,
            usable_at=existing.usable_at,
            created=False,
        )


class TiingoDailyPayload(BaseModel):
    """Job payload for kind 'data.tiingo_daily'."""

    ticker: str = Field(min_length=1, max_length=12)
    security_id: UUID4
    start_date: date
    end_date: date
    source_available_at: datetime | None = None
    source_available_basis: str | None = None


def make_tiingo_daily_handler(engine: AsyncEngine, client: TiingoClient):
    """Build the `data.tiingo_daily` job handler around an injected
    engine and client (tests use a mock transport; production wires
    the real token and rate limit from Settings)."""

    async def handle_tiingo_daily(claimed: ClaimedJob) -> dict:
        payload = TiingoDailyPayload.model_validate(claimed.payload)
        result = await ingest_daily(
            engine,
            client,
            security_id=payload.security_id,
            ticker=payload.ticker,
            start_date=payload.start_date,
            end_date=payload.end_date,
            source_available_at=payload.source_available_at,
            source_available_basis=payload.source_available_basis,
        )
        return {
            "raw_object_id": str(result.raw_object_id),
            "rows": result.rows,
            "usable_at": result.usable_at.isoformat(),
            "created": result.created,
        }

    return handle_tiingo_daily
