"""Unqualified Massive acquisition candidate; never creates a LIVE_RESEARCH snapshot.

Official endpoint references and missing qualification evidence are recorded in
docs/market-provider-research.md. Raw response evidence is retained in memory for
the eventual append-only ingestion path; this module does not imply persistence.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr

from yta_alpha.errors import DomainError

ORIGIN = "https://api.massive.com"
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 10
ACQUISITION_TIMEOUT = 60
REQUEST_INTERVAL = 15.1


def decode_payload(body: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Non-finite provider number")

    result = json.loads(
        body, parse_float=Decimal, parse_constant=nonfinite, object_pairs_hook=pairs
    )
    if not isinstance(result, dict):
        raise ValueError("Provider response must be an object")
    return result


@dataclass(frozen=True)
class RawPage:
    path: str
    query: tuple[tuple[str, str], ...]
    retrieved_at: datetime
    body: bytes

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def payload(self) -> dict:
        return decode_payload(self.body)


@dataclass(frozen=True)
class CandidateSplit:
    provider_id: str
    effective_date: date
    split_from: Decimal
    split_to: Decimal
    adjustment_type: str
    available_at: datetime
    raw_page_hash: str


@dataclass(frozen=True)
class Acquisition:
    bars: tuple[RawPage, ...]
    splits: tuple[RawPage, ...]
    split_events: tuple[CandidateSplit, ...]
    qualification_status: str = field(default="UNVERIFIED", init=False)


def normalize_splits(pages: list[RawPage], start: date, end: date) -> tuple[CandidateSplit, ...]:
    """Retain event ratios and provenance; do not attest coverage or session validity."""
    events, identifiers = [], set()
    previous = None
    for page in pages:
        for row in page.payload().get("results", []):
            if not isinstance(row, dict) or row.get("ticker") != "NVDA":
                raise ValueError("Invalid split record")
            identifier = row.get("id")
            if (
                not isinstance(identifier, str)
                or not identifier.strip()
                or identifier in identifiers
            ):
                raise ValueError("Missing or repeated split identity")
            raw_date = row.get("execution_date")
            if not isinstance(raw_date, str):
                raise ValueError("Missing execution date")
            day = date.fromisoformat(raw_date)
            if raw_date != day.isoformat() or not start <= day <= end:
                raise ValueError("Invalid execution date")
            if previous is not None and day <= previous:
                raise ValueError("Ambiguous or unordered split events")
            kind = row.get("adjustment_type")
            if kind not in ("forward_split", "reverse_split"):
                raise DomainError(
                    "UNSUPPORTED_CORPORATE_ACTION",
                    "Corporate action requires qualification",
                    status=502,
                )
            values = []
            for key in ("split_from", "split_to"):
                value = row.get(key)
                if type(value) not in (int, Decimal):
                    raise ValueError("Missing exact split ratio")
                value = Decimal(value)
                if not value.is_finite() or value <= 0:
                    raise ValueError("Invalid split ratio")
                values.append(value)
            before, after = values
            if (kind == "forward_split" and after <= before) or (
                kind == "reverse_split" and after >= before
            ):
                raise ValueError("Split direction disagrees with ratio")
            events.append(
                CandidateSplit(
                    identifier, day, before, after, kind, page.retrieved_at, page.content_hash
                )
            )
            identifiers.add(identifier)
            previous = day
    return tuple(events)


def aggregate_day(timestamp: int) -> date:
    if type(timestamp) is not int:
        raise ValueError("Expected integer aggregate timestamp")
    try:
        instant = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=timestamp)
        return instant.astimezone(ZoneInfo("America/New_York")).date()
    except (OverflowError, ValueError):
        raise ValueError("Invalid aggregate timestamp") from None


def validate_raw_bars(pages: list[RawPage], start: date, end: date) -> None:
    """Validate raw daily records without claiming exchange-session qualification.

    The provider timestamp denotes the aggregate start, not market close or
    historical availability. Missing dates remain missing; no prices are filled.
    """
    previous = None
    for page in pages:
        for row in page.payload().get("results", []):
            if not isinstance(row, dict):
                raise ValueError("Invalid bar record")
            values = {}
            for key in ("o", "h", "l", "c", "v"):
                value = row.get(key)
                if type(value) not in (int, Decimal):
                    raise ValueError("Expected exact numeric bar field")
                value = Decimal(value)
                if not value.is_finite() or value < 0 or (key != "v" and value == 0):
                    raise ValueError("Invalid bar value")
                values[key] = value
            if not (
                values["l"] <= values["o"] <= values["h"]
                and values["l"] <= values["c"] <= values["h"]
            ):
                raise ValueError("Inconsistent OHLC range")
            day = aggregate_day(row.get("t"))
            if not start <= day <= end or (previous is not None and day <= previous):
                raise ValueError("Out-of-scope, duplicate or unordered daily bar")
            previous = day


class MassiveCandidate:
    def __init__(self, token: SecretStr, *, transport=None, clock=None):
        value = token.get_secret_value()
        if not value or not value.isascii() or any(c.isspace() for c in value):
            raise ValueError("A private provider credential is required")
        self._token = token
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))

    async def acquire(self, start: date, end: date) -> Acquisition:
        if start > end or (end - start).days > 3 * 366:
            raise ValueError("Invalid acquisition date range")
        try:
            async with (
                asyncio.timeout(
                    ACQUISITION_TIMEOUT + (MAX_PAGES * 2 * REQUEST_INTERVAL if self._transport is None else 0)
                ),
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=5,
                    trust_env=False,
                    follow_redirects=False,
                    headers={
                        "Authorization": "Bearer " + self._token.get_secret_value(),
                        "Accept-Encoding": "identity",
                    },
                ) as client,
            ):
                bars = await self._pages(
                    client,
                    f"/v2/aggs/ticker/NVDA/range/1/day/{start.isoformat()}/{end.isoformat()}",
                    {"adjusted": "false", "sort": "asc", "limit": "50000"},
                    bars=True,
                )
                validate_raw_bars(bars, start, end)
                splits = await self._pages(
                    client,
                    "/stocks/v1/splits",
                    {
                        "ticker": "NVDA",
                        "execution_date.gte": start.isoformat(),
                        "execution_date.lte": end.isoformat(),
                        "sort": "execution_date.asc",
                        "limit": "5000",
                    },
                    bars=False,
                )
                events = normalize_splits(splits, start, end)
                return Acquisition(tuple(bars), tuple(splits), events)
        except (httpx.HTTPError, TimeoutError):
            raise DomainError(
                "PROVIDER_UNAVAILABLE", "Provider request failed", status=503, retryable=True
            ) from None
        except (ValueError, TypeError):
            raise DomainError(
                "INVALID_PROVIDER_RESPONSE", "Provider response rejected", status=502
            ) from None

    async def _pages(self, client, path: str, query: dict, *, bars: bool) -> list[RawPage]:
        pages, seen = [], set()
        initial_query = dict(query)
        while True:
            identity = tuple(sorted(query.items()))
            if identity in seen or len(pages) >= MAX_PAGES:
                raise ValueError("Pagination cycle or limit exceeded")
            seen.add(identity)
            # Personal free-tier collector: at most four starts per minute.
            # Mock/custom transports own their scheduling in protocol tests.
            if self._transport is None:
                await asyncio.sleep(REQUEST_INTERVAL)
            async with client.stream("GET", ORIGIN + path, params=query) as response:
                if response.status_code != 200:
                    code = {
                        401: "PROVIDER_AUTH_FAILED",
                        403: "PROVIDER_ACCESS_DENIED",
                        429: "PROVIDER_RATE_LIMITED",
                    }.get(response.status_code, "PROVIDER_UNAVAILABLE")
                    raise DomainError(
                        code,
                        "Provider request rejected",
                        status=503,
                        retryable=response.status_code == 429 or response.status_code >= 500,
                    )
                if (
                    response.headers.get("content-encoding", "identity") != "identity"
                    or response.headers.get("content-type", "").split(";")[0] != "application/json"
                ):
                    raise ValueError("Expected uncompressed JSON")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_PAGE_BYTES:
                        raise ValueError("Response too large")
            received = self._clock()
            if received.tzinfo is None:
                raise ValueError("Retrieval clock requires timezone")
            page = RawPage(path, identity, received.astimezone(UTC), bytes(content))
            payload = page.payload()
            if payload.get("status") != "OK" or not isinstance(payload.get("results", []), list):
                raise ValueError("Unsuccessful provider payload")
            if bars and (payload.get("adjusted") is not False or payload.get("ticker") != "NVDA"):
                raise ValueError("Expected raw NVDA bars")
            pages.append(page)
            next_url = payload.get("next_url")
            if next_url is None:
                return pages
            if not isinstance(next_url, str):
                raise ValueError("Invalid pagination link")
            url = urlsplit(next_url)
            if (
                url.scheme != "https"
                or url.hostname != "api.massive.com"
                or url.port not in (None, 443)
                or url.username is not None
                or url.password is not None
                or url.fragment
                or url.path != path
            ):
                raise ValueError("Pagination leaves the approved endpoint")
            items = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
            query = dict(items)
            if len(query) != len(items) or set(query) - {
                "cursor",
                "limit",
                "sort",
                "adjusted",
                "ticker",
                "execution_date.gte",
                "execution_date.lte",
            }:
                raise ValueError("Pagination parameters rejected")
            if not query.get("cursor"):
                raise ValueError("Pagination cursor is missing")
            if any(
                key != "cursor" and initial_query.get(key) != value for key, value in query.items()
            ):
                raise ValueError("Pagination changed the requested acquisition scope")
