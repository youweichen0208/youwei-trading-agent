"""Versioned local Parquet projections of immutable, unqualified acquisition evidence."""

import errno
import hashlib
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import SHA256, DataMode

COLUMNS = (
    "acquisition_hash",
    "provider",
    "symbol",
    "data_mode",
    "qualification_status",
    "price_basis",
    "timezone",
    "session_date",
    "aggregate_timestamp_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "published_at",
    "available_at",
    "retrieved_at",
    "source_page_hash",
    "page_index",
    "row_index",
)
INTEGERS = {"aggregate_timestamp_ms", "page_index", "row_index"}


class Partition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(pattern=r"^symbol=NVDA/year=[0-9]{4}/bars\.parquet$")
    sha256: SHA256
    rows: int = Field(gt=0)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["candidate-market-lake-v1"] = "candidate-market-lake-v1"
    acquisition_hash: SHA256
    data_mode: DataMode
    provider: Literal["massive"] = "massive"
    symbol: Literal["NVDA"] = "NVDA"
    qualification_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    price_encoding: Literal["decimal-string"] = "decimal-string"
    rows: int = Field(ge=0)
    rows_sha256: SHA256
    partitions: list[Partition]


def connection():
    # No automatic extension downloads or network-backed scanners.
    return duckdb.connect(
        config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "threads": 2,
            "memory_limit": "256MB",
            "temp_directory": "",
        }
    )


def sync(path: Path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def location(root: Path, digest: str, mode: DataMode) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid acquisition hash")
    return root / DataMode(mode).value / digest


def read_rows(directory: Path, manifest: Manifest) -> list[dict]:
    files = []
    paths = [part.path for part in manifest.partitions]
    if paths != sorted(set(paths)):
        raise ValueError("Duplicate or unordered partitions")
    for part in manifest.partitions:
        path = directory / part.path
        if path.resolve() != directory.resolve() / part.path:
            raise ValueError("Symlink in partition path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != part.sha256:
            raise ValueError("Partition hash mismatch")
        files.append(str(path))
    if not files:
        rows = []
    else:
        with connection() as db:
            cursor = db.execute(
                "SELECT * FROM read_parquet(?, hive_partitioning=false) ORDER BY session_date",
                [files],
            )
            if tuple(col[0] for col in cursor.description) != COLUMNS:
                raise ValueError("Unexpected Parquet columns")
            rows = [dict(zip(COLUMNS, values, strict=True)) for values in cursor.fetchall()]
    if len(rows) != manifest.rows or content_hash(rows) != manifest.rows_sha256:
        raise ValueError("Projection content mismatch")
    counts = defaultdict(int)
    for row in rows:
        if any(
            row[key] != expected
            for key, expected in (
                ("acquisition_hash", manifest.acquisition_hash),
                ("data_mode", manifest.data_mode.value),
                ("symbol", "NVDA"),
                ("provider", "massive"),
                ("qualification_status", "UNVERIFIED"),
                ("price_basis", "RAW"),
            )
        ):
            raise ValueError("Projection scope mismatch")
        counts[f"symbol=NVDA/year={row['session_date'][:4]}/bars.parquet"] += 1
    if dict(counts) != {part.path: part.rows for part in manifest.partitions}:
        raise ValueError("Partition counts mismatch")
    return rows


def load(root: Path, digest: str, mode: DataMode) -> tuple[Manifest, list[dict]]:
    directory = location(root, digest, mode)
    try:
        path = directory / "manifest.json"
        if path.is_symlink() or directory.resolve() != root.resolve() / mode.value / digest:
            raise ValueError("Symlink in manifest path")
        manifest = Manifest.model_validate_json(path.read_bytes())
        if manifest.acquisition_hash != digest or manifest.data_mode != mode:
            raise ValueError("Manifest scope mismatch")
        return manifest, read_rows(directory, manifest)
    except (ValueError, OSError, duckdb.Error, KeyError, TypeError):
        raise DomainError(
            "MARKET_LAKE_INVALID", "Market archive is missing or inconsistent", status=503
        ) from None


def export(store: AcquisitionStore, root: Path, digest: str, mode: DataMode) -> Manifest:
    """Publish a complete directory atomically; retries verify rather than overwrite."""
    mode = DataMode(mode)
    target = location(root, digest, mode)
    rows = store.daily_bars(digest, data_mode=mode)
    rows_hash = content_hash(rows)
    if target.exists():
        existing, archived = load(root, digest, mode)
        if archived != rows:
            raise DomainError("MARKET_LAKE_CONFLICT", "Archive disagrees with database", status=409)
        return existing
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=target.parent))
    try:
        partitions = []
        by_year = defaultdict(list)
        for row in rows:
            by_year[row["session_date"][:4]].append(tuple(row[key] for key in COLUMNS))
        with connection() as db:
            columns = ",".join(
                f'"{key}" {"BIGINT" if key in INTEGERS else "VARCHAR"}' for key in COLUMNS
            )
            db.execute("CREATE TABLE bars (" + columns + ")")
            for year, values in sorted(by_year.items()):
                path = staging / f"symbol=NVDA/year={year}/bars.parquet"
                path.parent.mkdir(parents=True, mode=0o700)
                db.execute("DELETE FROM bars")
                db.executemany(
                    "INSERT INTO bars VALUES (" + ",".join("?" for _ in COLUMNS) + ")", values
                )
                db.execute("COPY bars TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])
                path.chmod(0o600)
                sync(path)
                partitions.append(
                    Partition(
                        path=str(path.relative_to(staging)),
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        rows=len(values),
                    )
                )
                sync(path.parent)
        manifest = Manifest(
            acquisition_hash=digest,
            data_mode=mode,
            rows=len(rows),
            rows_sha256=rows_hash,
            partitions=partitions,
        )
        if read_rows(staging, manifest) != rows:
            raise ValueError("Parquet roundtrip mismatch")
        path = staging / "manifest.json"
        path.write_text(manifest.model_dump_json(indent=2) + "\n")
        path.chmod(0o600)
        sync(path)
        for directory in (staging / "symbol=NVDA", staging):
            if directory.exists():
                sync(directory)
        try:
            staging.rename(target)
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            existing, archived = load(root, digest, mode)
            if archived != rows:
                raise DomainError(
                    "MARKET_LAKE_CONFLICT", "Concurrent archive disagrees", status=409
                ) from None
            return existing
        sync(target.parent)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def query(root: Path, digest: str, mode: DataMode, *, symbol: str, year: int) -> list[dict]:
    if symbol != "NVDA" or type(year) is not int or not 1900 <= year <= 9999:
        raise ValueError("Expected NVDA and a valid year")
    manifest, verified = load(root, digest, DataMode(mode))
    parts = [
        part
        for part in manifest.partitions
        if part.path == f"symbol={symbol}/year={year}/bars.parquet"
    ]
    if not parts:
        return []
    path = location(root, digest, mode) / parts[0].path
    with connection() as db:
        cursor = db.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false) WHERE symbol=? "
            "AND session_date >= ? AND session_date <= ? ORDER BY session_date",
            [str(path), symbol, f"{year:04d}-01-01", f"{year:04d}-12-31"],
        )
        rows = [dict(zip(COLUMNS, values, strict=True)) for values in cursor.fetchall()]
    if rows != [row for row in verified if row["session_date"].startswith(f"{year:04d}-")]:
        raise DomainError(
            "MARKET_LAKE_INVALID", "Partition query disagrees with archive", status=503
        )
    return rows
