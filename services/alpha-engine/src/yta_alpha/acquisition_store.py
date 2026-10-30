"""Append-only raw provider evidence; saving does not qualify data for research."""

import re
from dataclasses import dataclass
from datetime import UTC, date
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.market_bars import COLUMNS, persist_bars, projected_rows
from yta_alpha.massive import (
    MAX_PAGE_BYTES,
    MAX_PAGES,
    Acquisition,
    RawPage,
    normalize_splits,
    validate_raw_bars,
)
from yta_alpha.models import DataMode

LOCK_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT_MS = 15000
FAILURE_CODES = {
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_ACCESS_DENIED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "INVALID_PROVIDER_RESPONSE",
    "UNSUPPORTED_CORPORATE_ACTION",
    "ACQUISITION_FAILED",
}


def acquisition_connection(database_url: str, **kwargs):
    """Bound server-side lock/statement waits for each operator DB connection."""
    return psycopg.connect(
        database_url,
        connect_timeout=5,
        options=f"-c lock_timeout={LOCK_TIMEOUT_MS} -c statement_timeout={STATEMENT_TIMEOUT_MS}",
        **kwargs,
    )


@dataclass(frozen=True)
class StoredAcquisition:
    start: date
    end: date
    data_mode: DataMode
    acquisition: Acquisition


class AcquisitionStore:
    def __init__(self, database_url: str):
        self._url = database_url

    def start_attempt(self, start: date, end: date, data_mode: DataMode) -> UUID:
        attempt = uuid4()
        with acquisition_connection(self._url) as connection:
            connection.execute(
                "INSERT INTO yta.audit_events(action,resource_id,payload) "
                "VALUES ('provider.acquire_started',%s,%s)",
                (
                    str(attempt),
                    Jsonb(
                        {
                            "provider": "massive",
                            "symbol": "NVDA",
                            "requested_start": start.isoformat(),
                            "requested_end": end.isoformat(),
                            "data_mode": DataMode(data_mode).value,
                        }
                    ),
                ),
            )
        return attempt

    @staticmethod
    def _finish_attempt(connection, attempt: UUID, action: str, payload: dict, scope=None):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"acquire-attempt:{attempt}",)
        )
        started = connection.execute(
            "SELECT payload FROM yta.audit_events WHERE resource_id=%s "
            "AND action='provider.acquire_started'",
            (str(attempt),),
        ).fetchone()
        if started is None or (scope and any(started[0].get(k) != scope[k] for k in started[0])):
            raise DomainError("ACQUISITION_ATTEMPT_MISMATCH", "Acquisition attempt scope differs")
        previous = connection.execute(
            "SELECT action,payload FROM yta.audit_events WHERE resource_id=%s "
            "AND action IN ('provider.acquire_completed','provider.acquire_failed')",
            (str(attempt),),
        ).fetchone()
        if previous:
            if previous != (action, payload):
                raise DomainError(
                    "ACQUISITION_ATTEMPT_FINISHED", "Acquisition attempt already ended"
                )
            return
        connection.execute(
            "INSERT INTO yta.audit_events(action,resource_id,payload) VALUES (%s,%s,%s)",
            (action, str(attempt), Jsonb(payload)),
        )

    def fail_attempt(self, attempt: UUID, code: str) -> None:
        if code not in FAILURE_CODES:
            code = "ACQUISITION_FAILED"
        with acquisition_connection(self._url) as connection:
            self._finish_attempt(
                connection, attempt, "provider.acquire_failed", {"error_code": code}
            )

    def get_attempt(self, attempt: UUID, *, data_mode: DataMode) -> dict:
        mode = DataMode(data_mode)
        with acquisition_connection(self._url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT action,payload,occurred_at FROM yta.audit_events WHERE resource_id=%s "
                "AND action IN ('provider.acquire_started','provider.acquire_completed',"
                "'provider.acquire_failed') ORDER BY id LIMIT 3",
                (str(attempt),),
            ).fetchall()
        if not rows or rows[0]["payload"].get("data_mode") != mode.value:
            raise DomainError("NOT_FOUND", "Acquisition attempt not found", status=404)
        try:
            scope = rows[0]["payload"]
            if len(rows) > 2 or rows[0]["action"] != "provider.acquire_started":
                raise ValueError("Invalid attempt event sequence")
            start, end = (
                date.fromisoformat(scope["requested_start"]),
                date.fromisoformat(scope["requested_end"]),
            )
            if (scope["provider"], scope["symbol"]) != ("massive", "NVDA") or start > end:
                raise ValueError("Invalid attempt scope")
            result = {
                "attempt_id": str(attempt),
                "data_mode": mode.value,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "started_at": rows[0]["occurred_at"].isoformat(),
                "finished_at": None,
                "status": "UNRESOLVED",
                "qualification_status": "UNVERIFIED",
            }
            if len(rows) == 2:
                terminal = rows[1]
                payload = terminal["payload"]
                result["finished_at"] = terminal["occurred_at"].isoformat()
                if terminal["action"] == "provider.acquire_completed":
                    digest = payload["acquisition_hash"]
                    saved = self.load(digest, data_mode=mode)
                    if (saved.start, saved.end) != (start, end):
                        raise ValueError("Attempt evidence scope mismatch")
                    result.update(
                        status="COMPLETED", acquisition_hash=digest, integrity_checked=True
                    )
                elif (
                    terminal["action"] == "provider.acquire_failed"
                    and payload["error_code"] in FAILURE_CODES
                ):
                    result.update(status="FAILED", error_code=payload["error_code"])
                else:
                    raise ValueError("Invalid terminal event")
        except (ValueError, KeyError, TypeError, DomainError):
            raise DomainError(
                "ACQUISITION_ATTEMPT_INTEGRITY_FAILED", "Attempt audit is inconsistent", status=500
            ) from None
        return result

    @staticmethod
    def _manifest(acquisition: Acquisition, start: date, end: date, data_mode: DataMode) -> dict:
        if start > end or (end - start).days > 3 * 366:
            raise ValueError("Invalid acquisition date range")
        if acquisition.qualification_status != "UNVERIFIED":
            raise ValueError("Acquisition cannot assert qualification")
        validate_raw_bars(list(acquisition.bars), start, end)
        if normalize_splits(list(acquisition.splits), start, end) != acquisition.split_events:
            raise ValueError("Normalized events disagree with raw evidence")
        manifest = {
            "version": "provider-acquisition-v1",
            "provider": "massive",
            "symbol": "NVDA",
            "data_mode": DataMode(data_mode).value,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "qualification_status": "UNVERIFIED",
            "pages": [],
        }
        for kind, pages in (("bars", acquisition.bars), ("splits", acquisition.splits)):
            if not 1 <= len(pages) <= MAX_PAGES:
                raise ValueError("Incomplete or oversized acquisition")
            expected_path = (
                f"/v2/aggs/ticker/NVDA/range/1/day/{start.isoformat()}/{end.isoformat()}"
                if kind == "bars"
                else "/stocks/v1/splits"
            )
            for index, page in enumerate(pages):
                if page.path != expected_path or not 1 <= len(page.body) <= MAX_PAGE_BYTES:
                    raise ValueError("Invalid raw evidence scope or size")
                if page.retrieved_at.utcoffset() is None:
                    raise ValueError("Retrieval time must be timezone aware")
                if any(
                    key
                    not in {
                        "cursor",
                        "limit",
                        "sort",
                        "adjusted",
                        "ticker",
                        "execution_date.gte",
                        "execution_date.lte",
                    }
                    for key, _ in page.query
                ):
                    raise ValueError("Unexpected acquisition query field")
                manifest["pages"].append(
                    {
                        "kind": kind,
                        "page_index": index,
                        "path": page.path,
                        "query": [list(pair) for pair in page.query],
                        "content_hash": page.content_hash,
                        "retrieved_at": page.retrieved_at.astimezone(UTC).isoformat(),
                    }
                )
        return manifest

    def save(
        self,
        acquisition: Acquisition,
        *,
        start: date,
        end: date,
        data_mode: DataMode,
        attempt_id: UUID | None = None,
    ) -> str:
        """Same evidence retries deduplicate; later retrievals/revisions append new facts."""
        manifest = self._manifest(acquisition, start, end, data_mode)
        digest = content_hash(manifest)
        bars = projected_rows(acquisition)
        with acquisition_connection(self._url) as connection:
            if attempt_id is not None:
                self._finish_attempt(
                    connection,
                    attempt_id,
                    "provider.acquire_completed",
                    {"acquisition_hash": digest},
                    scope=manifest,
                )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"acquisition:{digest}",)
            )
            if connection.execute(
                "SELECT hash FROM yta.provider_acquisitions WHERE hash=%s", (digest,)
            ).fetchone():
                persist_bars(connection, digest, bars)
                return digest
            connection.execute(
                "INSERT INTO yta.provider_acquisitions "
                "(hash,provider,symbol,data_mode,requested_start,requested_end,"
                "qualification_status,manifest) "
                "VALUES (%s,'massive','NVDA',%s,%s,%s,'UNVERIFIED',%s)",
                (digest, manifest["data_mode"], start, end, Jsonb(manifest)),
            )
            for kind, pages in (("bars", acquisition.bars), ("splits", acquisition.splits)):
                for index, page in enumerate(pages):
                    connection.execute(
                        "INSERT INTO yta.provider_response_pages "
                        "(acquisition_hash,kind,page_index,content_hash,retrieved_at,body) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (digest, kind, index, page.content_hash, page.retrieved_at, page.body),
                    )
            persist_bars(connection, digest, bars)
            connection.execute(
                "INSERT INTO yta.audit_events(action,resource_id,payload) VALUES (%s,%s,%s)",
                (
                    "provider.acquire",
                    digest,
                    Jsonb(
                        {"data_mode": manifest["data_mode"], "qualification_status": "UNVERIFIED"}
                    ),
                ),
            )
        return digest

    def daily_bars(self, digest: str, *, data_mode: DataMode) -> list[dict]:
        """Read one immutable acquisition, checking projection against original evidence.

        Missing legacy projections fail explicitly; retry save to materialize them.
        No latest-price selection, gap filling or research qualification is implied.
        """
        saved = self.load(digest, data_mode=data_mode)
        expected = projected_rows(saved.acquisition)
        with acquisition_connection(self._url) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM yta.market_bars "
                "WHERE acquisition_hash=%s ORDER BY session_date",
                (digest,),
            ).fetchall()
        if rows != expected:
            raise DomainError(
                "MARKET_BARS_INTEGRITY_FAILED", "Daily records disagree with evidence", status=500
            )
        result = []
        for row in rows:
            page = saved.acquisition.bars[row[0]]
            result.append(
                {
                    "acquisition_hash": digest,
                    "provider": "massive",
                    "symbol": "NVDA",
                    "data_mode": saved.data_mode.value,
                    "qualification_status": "UNVERIFIED",
                    "price_basis": "RAW",
                    "timezone": "America/New_York",
                    "session_date": row[2].isoformat(),
                    "aggregate_timestamp_ms": row[3],
                    **dict(
                        zip(
                            ("open", "high", "low", "close", "volume"),
                            (str(value) for value in row[4:]),
                            strict=True,
                        )
                    ),
                    "published_at": None,
                    "available_at": page.retrieved_at.astimezone(UTC).isoformat(),
                    "retrieved_at": page.retrieved_at.astimezone(UTC).isoformat(),
                    "source_page_hash": page.content_hash,
                    "page_index": row[0],
                    "row_index": row[1],
                }
            )
        return result

    def load(self, digest: str, *, data_mode: DataMode) -> StoredAcquisition:
        """Reconstruct and verify evidence within one explicit data-mode scope."""
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid acquisition hash")
        mode = DataMode(data_mode)
        with acquisition_connection(self._url, row_factory=dict_row) as connection:
            record = connection.execute(
                "SELECT * FROM yta.provider_acquisitions WHERE hash=%s AND data_mode=%s",
                (digest, mode.value),
            ).fetchone()
            if record is None:
                raise DomainError("NOT_FOUND", "Acquisition not found", status=404)
            pages = connection.execute(
                "SELECT * FROM yta.provider_response_pages WHERE acquisition_hash=%s "
                "ORDER BY kind,page_index",
                (digest,),
            ).fetchall()
        try:
            manifest = record["manifest"]
            if content_hash(manifest) != digest or len(pages) != len(manifest["pages"]):
                raise ValueError("Manifest hash or page count mismatch")
            grouped = {"bars": [], "splits": []}
            for row, description in zip(pages, manifest["pages"], strict=True):
                kind = row["kind"]
                if (kind, row["page_index"]) != (description["kind"], description["page_index"]):
                    raise ValueError("Page ordering mismatch")
                if row["page_index"] != len(grouped[kind]):
                    raise ValueError("Page sequence is incomplete")
                page = RawPage(
                    description["path"],
                    tuple(tuple(pair) for pair in description["query"]),
                    row["retrieved_at"],
                    bytes(row["body"]),
                )
                if page.content_hash != row["content_hash"]:
                    raise ValueError("Raw page hash mismatch")
                grouped[kind].append(page)
            start, end = record["requested_start"], record["requested_end"]
            acquisition = Acquisition(
                tuple(grouped["bars"]),
                tuple(grouped["splits"]),
                normalize_splits(grouped["splits"], start, end),
            )
            if self._manifest(acquisition, start, end, mode) != manifest:
                raise ValueError("Stored metadata disagrees with evidence")
            if (record["provider"], record["symbol"], record["qualification_status"]) != (
                "massive",
                "NVDA",
                "UNVERIFIED",
            ):
                raise ValueError("Invalid qualification metadata")
        except (KeyError, ValueError, TypeError, DomainError):
            raise DomainError(
                "ACQUISITION_INTEGRITY_FAILED",
                "Stored acquisition evidence is inconsistent",
                status=500,
            ) from None
        return StoredAcquisition(start, end, mode, acquisition)
