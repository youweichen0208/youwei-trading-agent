"""Canonical symbols and immutable reference history; not provider qualification."""

import re
from datetime import date
from typing import Literal
from uuid import UUID

from psycopg.rows import dict_row
from pydantic import AwareDatetime

from yta_alpha.acquisition_store import acquisition_connection
from yta_alpha.errors import DomainError
from yta_alpha.models import Contract

CANONICAL = re.compile(r"[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?", re.ASCII)


def normalize_symbol(value: str) -> str:
    # Do not map Unicode lookalikes or remove interior whitespace/punctuation.
    if not isinstance(value, str) or not value.isascii() or len(value) > 32:
        raise DomainError("INVALID_SYMBOL", "Invalid security symbol")
    canonical = value.strip(" \t\r\n").upper()
    if not CANONICAL.fullmatch(canonical):
        raise DomainError("INVALID_SYMBOL", "Invalid security symbol")
    return canonical


class SymbolVersion(Contract):
    id: UUID
    symbol: str
    version: int
    status: Literal["UNVERIFIED", "ACTIVE", "RENAMED", "DELISTED"]
    effective_from: date | None
    effective_to: date | None
    successor_symbol: str | None
    source_id: str
    recorded_at: AwareDatetime


class SymbolHistory(Contract):
    symbol: str
    versions: tuple[SymbolVersion, ...]
    qualification_status: Literal["UNVERIFIED"] = "UNVERIFIED"


def symbol_history(database_url: str, symbol: str) -> SymbolHistory:
    canonical = normalize_symbol(symbol)
    # Registry history is not authorization to expand the MVP research universe.
    if canonical != "NVDA":
        raise DomainError("UNSUPPORTED_SCOPE", "MVP supports NVDA only")
    with acquisition_connection(database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute(
            "SELECT id,symbol,version,status,effective_from,effective_to,successor_symbol,"
            "source_id,recorded_at FROM yta.symbols WHERE symbol=%s ORDER BY version LIMIT 1001",
            (canonical,),
        ).fetchall()
    if not rows:
        raise DomainError("SYMBOL_UNAVAILABLE", "Symbol reference is unavailable", status=503)
    if len(rows) > 1000:
        raise DomainError(
            "SYMBOL_UNAVAILABLE", "Symbol history exceeds supported limit", status=503
        )
    return SymbolHistory(
        symbol=canonical, versions=tuple(SymbolVersion.model_validate(r) for r in rows)
    )
