"""Frozen evidence snapshots (architecture section 4).

A snapshot is a materialized, content-addressed selection of PIT data
plus a manifest: the exact query, time mode, securities, referenced
raw-object versions, schema, row counts, content hash, source vendor
versions and license tags, and the snapshot code version. Freezing
runs through the PIT query, so a snapshot at as-of T contains exactly
what a controller at T could have seen — backfills and later
corrections cannot change it (re-freezing the same query at the same
as_of returns the identical snapshot).

Content lives inline in MVP; the hash contract (content_sha256 over
the canonical JSON) survives a later move to object storage.

Authorization seam: capability scopes reference snapshots via
snapshot_scope(snapshot_id) — downstream systems (Runner, S03; Hermes
controller, S07) verify that scope before reading.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.pit import MODES, daily_bars_asof
from youwei_core.db.meta import data_sources, raw_objects, snapshots

SNAPSHOT_CODE_VERSION = "daily-bars-snapshot-v1"
SCHEMA = "daily-bars-v1"
KIND = "daily_bars"


class SnapshotNotFound(Exception):
    pass


class SnapshotCorrupt(Exception):
    """Content/manifest hash mismatch: the snapshot must not be
    served and the incident needs investigation, not a silent refetch."""


@dataclass
class SnapshotRef:
    snapshot_id: uuid.UUID
    content_sha256: str
    row_count: int
    created: bool
    manifest: dict


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def snapshot_scope(snapshot_id) -> str:
    """The capability scope string for reading this snapshot."""
    return f"snapshot:{snapshot_id}"


async def freeze_daily_bars(
    engine: AsyncEngine,
    security_ids: list,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
    mode: str,
    created_by_attempt: uuid.UUID | None = None,
) -> SnapshotRef:
    """Freeze the PIT daily bars for the given securities and range.

    Missing data is recorded in the manifest (coverage per security),
    never fabricated: a security with no usable rows at as_of appears
    with rows=0 — that absence is part of the evidence.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    bars = await daily_bars_asof(
        engine, security_ids, start_date, end_date, as_of=as_of, mode=mode
    )
    content = _canonical_json(bars)
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    query = {
        "kind": KIND,
        "security_ids": sorted(str(s) for s in security_ids),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "as_of": as_of.isoformat(),
        "mode": mode,
    }
    query_sha = hashlib.sha256(_canonical_json(query).encode("utf-8")).hexdigest()

    # provenance: distinct raw objects actually used + their sources
    raw_ids = sorted({bar["provenance"]["raw_object_id"] for bar in bars})
    manifest = await _build_manifest(
        engine, query, bars, raw_ids, content_sha, created_by_attempt
    )

    try:
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(snapshots).where(
                        snapshots.c.kind == KIND,
                        snapshots.c.query_sha256 == query_sha,
                        snapshots.c.content_sha256 == content_sha,
                    )
                )
            ).mappings().first()
            if existing is not None:
                return SnapshotRef(
                    snapshot_id=existing.id,
                    content_sha256=existing.content_sha256,
                    row_count=manifest["row_count"],
                    created=False,
                    manifest=existing.manifest,
                )

            snapshot_id = uuid.uuid4()
            await conn.execute(
                snapshots.insert().values(
                    id=snapshot_id,
                    kind=KIND,
                    query=query,
                    query_sha256=query_sha,
                    as_of=as_of,
                    mode=mode,
                    manifest=manifest,
                    content=content,
                    content_sha256=content_sha,
                    created_by_attempt=created_by_attempt,
                )
            )
        return SnapshotRef(
            snapshot_id=snapshot_id,
            content_sha256=content_sha,
            row_count=manifest["row_count"],
            created=True,
            manifest=manifest,
        )
    except IntegrityError:
        # concurrent freeze of the identical query+content
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(snapshots).where(
                        snapshots.c.kind == KIND,
                        snapshots.c.query_sha256 == query_sha,
                        snapshots.c.content_sha256 == content_sha,
                    )
                )
            ).mappings().one()
        return SnapshotRef(
            snapshot_id=existing.id,
            content_sha256=existing.content_sha256,
            row_count=manifest["row_count"],
            created=False,
            manifest=existing.manifest,
        )


async def _build_manifest(
    engine: AsyncEngine,
    query: dict,
    bars: list[dict],
    raw_ids: list[str],
    content_sha: str,
    created_by_attempt: uuid.UUID | None,
) -> dict:
    coverage: dict[str, dict] = {
        str(s): {"security_id": str(s), "rows": 0, "first": None, "last": None}
        for s in query["security_ids"]
    }
    for bar in bars:
        entry = coverage[bar["security_id"]]
        entry["rows"] += 1
        if entry["first"] is None or bar["trade_date"] < entry["first"]:
            entry["first"] = bar["trade_date"]
        if entry["last"] is None or bar["trade_date"] > entry["last"]:
            entry["last"] = bar["trade_date"]

    async with engine.begin() as conn:
        raw_rows = (
            (
                await conn.execute(
                    select(
                        raw_objects.c.id,
                        raw_objects.c.content_sha256,
                        raw_objects.c.ingested_at,
                        raw_objects.c.usable_at,
                        raw_objects.c.source_available_at,
                        raw_objects.c.row_count,
                        raw_objects.c.source_id,
                    ).where(raw_objects.c.id.in_(raw_ids))
                )
            )
            .mappings()
            .all()
            if raw_ids
            else []
        )
        source_ids = sorted({r.source_id for r in raw_rows})
        source_rows = (
            (
                await conn.execute(
                    select(
                        data_sources.c.id,
                        data_sources.c.name,
                        data_sources.c.vendor_version,
                        data_sources.c.license_tags,
                    ).where(data_sources.c.id.in_(source_ids))
                )
            )
            .mappings()
            .all()
            if source_ids
            else []
        )

    return {
        "kind": KIND,
        "query": query,
        "schema": SCHEMA,
        "row_count": len(bars),
        "coverage": [coverage[s] for s in sorted(coverage)],
        "raw_objects": [
            {
                "id": str(r.id),
                "content_sha256": r.content_sha256,
                "ingested_at": r.ingested_at.isoformat(),
                "usable_at": r.usable_at.isoformat(),
                "source_available_at": (
                    r.source_available_at.isoformat()
                    if r.source_available_at is not None
                    else None
                ),
                "row_count": r.row_count,
                "source_id": r.source_id,
            }
            for r in sorted(raw_rows, key=lambda r: str(r.id))
        ],
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "vendor_version": s.vendor_version,
                "license_tags": s.license_tags,
            }
            for s in source_rows
        ],
        "content_sha256": content_sha,
        "code_version": SNAPSHOT_CODE_VERSION,
        "created_by_attempt": (
            str(created_by_attempt) if created_by_attempt else None
        ),
    }


async def read_snapshot(engine: AsyncEngine, snapshot_id: uuid.UUID) -> dict:
    """Return {id, manifest, content}. The content hash is verified
    against the manifest on every read."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(snapshots).where(snapshots.c.id == snapshot_id)
            )
        ).mappings().first()
    if row is None:
        raise SnapshotNotFound(str(snapshot_id))
    actual = hashlib.sha256(row.content.encode("utf-8")).hexdigest()
    if actual != row.content_sha256 or row.manifest.get("content_sha256") != actual:
        raise SnapshotCorrupt(
            f"snapshot {snapshot_id} failed content hash verification"
        )
    return {"id": str(snapshot_id), "manifest": row.manifest, "content": row.content}


async def verify_snapshot(engine: AsyncEngine, snapshot_id: uuid.UUID) -> dict:
    """Independent audit: recompute the content hash, check the
    manifest agrees, and confirm every referenced raw object still
    exists with the recorded hash."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(snapshots).where(snapshots.c.id == snapshot_id)
            )
        ).mappings().first()
    if row is None:
        raise SnapshotNotFound(str(snapshot_id))

    actual = hashlib.sha256(row.content.encode("utf-8")).hexdigest()
    checks = {
        "content_hash_matches": actual == row.content_sha256,
        "manifest_hash_matches": row.manifest.get("content_sha256") == actual,
    }

    refs = row.manifest.get("raw_objects", [])
    if refs:
        async with engine.begin() as conn:
            raw_rows = (
                (
                    await conn.execute(
                        select(raw_objects.c.id, raw_objects.c.content_sha256).where(
                            raw_objects.c.id.in_([r["id"] for r in refs])
                        )
                    )
                )
                .mappings()
                .all()
            )
        by_id = {str(r.id): r.content_sha256 for r in raw_rows}
        checks["raw_objects_intact"] = all(
            by_id.get(ref["id"]) == ref["content_sha256"] for ref in refs
        )
    else:
        checks["raw_objects_intact"] = True
    checks["ok"] = all(checks.values())
    return checks
