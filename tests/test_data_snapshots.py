"""S04b: frozen snapshots — content-addressed PIT selections with full
provenance manifests.

Acceptance mapped from the plan (S04):
- 回补不能改变旧快照: re-freezing the same query at the same as_of
  after later ingests/corrections returns the identical snapshot
- manifest records the query, time mode, securities, raw-object
  versions, schema, row counts, content hash, vendor versions and
  license tags, code version
- missing data is recorded (rows=0), never fabricated
- reads verify the content hash; the audit verifier also confirms the
  referenced raw objects are intact
"""

import asyncio
import json
from datetime import UTC, date, datetime

import pytest

from youwei_core.data.pit import daily_bars_asof
from youwei_core.data.snapshots import (
    SnapshotCorrupt,
    SnapshotNotFound,
    freeze_daily_bars,
    read_snapshot,
    snapshot_scope,
    verify_snapshot,
)
from test_data_pit import _client, _ingest, _row, _security


async def _bars_fixture(engine, bodies=None):
    """Ingest the standard AAPL week fixture; returns (security, ingest)."""
    sec = await _security(engine)
    bodies = bodies or {"AAPL": json.dumps([_row("2026-09-25", 341.07)])}
    result = await _ingest(
        engine, bodies, "AAPL", sec, date(2026, 9, 21), date(2026, 9, 25)
    )
    return sec, result


async def test_freeze_creates_manifest_with_full_provenance(db_engine):
    sec, ingest = await _bars_fixture(
        db_engine,
        bodies={"AAPL": json.dumps([_row("2026-09-24", 335.92), _row("2026-09-25", 341.07)])},
    )
    ref = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )
    assert ref.created is True
    assert ref.row_count == 2

    m = ref.manifest
    assert m["kind"] == "daily_bars"
    assert m["schema"] == "daily-bars-v1"
    assert m["query"]["security_ids"] == [str(sec)]
    assert m["query"]["mode"] == "forward"
    assert m["coverage"][0]["rows"] == 2
    assert m["coverage"][0]["first"] == "2026-09-24"
    assert m["coverage"][0]["last"] == "2026-09-25"
    assert [r["id"] for r in m["raw_objects"]] == [str(ingest.raw_object_id)]
    assert m["raw_objects"][0]["content_sha256"]
    assert m["sources"][0]["id"] == "tiingo"
    assert "vendor:tiingo" in m["sources"][0]["license_tags"]
    assert m["code_version"] == "daily-bars-snapshot-v1"
    assert len(m["content_sha256"]) == 64

    # scope string for capability tokens
    assert snapshot_scope(ref.snapshot_id) == f"snapshot:{ref.snapshot_id}"


async def test_freeze_records_missing_data_never_fabricates(db_engine):
    sec, ingest = await _bars_fixture(db_engine)
    other = await _security(db_engine, ticker="MISS")
    # 'other' has no data at all: coverage must record rows=0

    ref = await freeze_daily_bars(
        db_engine, [sec, other], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )
    coverage = {c["security_id"]: c for c in ref.manifest["coverage"]}
    assert coverage[str(sec)]["rows"] == 1
    assert coverage[str(other)]["rows"] == 0
    assert coverage[str(other)]["first"] is None
    assert ref.row_count == 1  # only real rows counted


async def test_refreeze_same_asof_is_identical_after_corrections(db_engine):
    """The S04 acceptance: later vendor corrections cannot change what
    an old as_of froze."""
    sec, v1 = await _bars_fixture(
        db_engine, bodies={"AAPL": json.dumps([_row("2026-09-25", 341.07)])}
    )
    cutoff = v1.usable_at

    ref1 = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=cutoff, mode="forward",
    )
    assert ref1.created is True

    # later: vendor corrects the close AND backfills an extra day
    await asyncio.sleep(0.02)
    v2 = await _ingest(
        db_engine,
        {"AAPL": json.dumps([_row("2026-09-24", 335.92), _row("2026-09-25", 341.10)])},
        "AAPL", sec, date(2026, 9, 21), date(2026, 9, 25),
    )

    ref2 = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=cutoff, mode="forward",
    )
    assert ref2.created is False  # identical query+content -> same snapshot
    assert ref2.snapshot_id == ref1.snapshot_id

    snap = await read_snapshot(db_engine, ref1.snapshot_id)
    bars = json.loads(snap["content"])
    assert len(bars) == 1
    assert bars[0]["close"] == 341.07  # the corrected 341.10 did NOT leak in

    # a later as_of freezes the corrected view as a NEW snapshot
    ref3 = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=v2.usable_at, mode="forward",
    )
    assert ref3.created is True
    assert ref3.snapshot_id != ref1.snapshot_id
    snap3 = await read_snapshot(db_engine, ref3.snapshot_id)
    bars3 = json.loads(snap3["content"])
    assert {b["close"] for b in bars3} == {335.92, 341.10}


async def test_read_and_verify_snapshot(db_engine):
    sec, ingest = await _bars_fixture(db_engine)
    ref = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )

    snap = await read_snapshot(db_engine, ref.snapshot_id)
    assert snap["id"] == str(ref.snapshot_id)
    assert json.loads(snap["content"])[0]["close"] == 341.07

    checks = await verify_snapshot(db_engine, ref.snapshot_id)
    assert checks["ok"] is True
    assert checks["content_hash_matches"] is True
    assert checks["raw_objects_intact"] is True

    with pytest.raises(SnapshotNotFound):
        await read_snapshot(db_engine, "00000000-0000-0000-0000-000000000000")


async def test_verify_detects_tampered_content(db_engine):
    from sqlalchemy import update

    from youwei_core.db.meta import snapshots as snapshots_t

    sec, ingest = await _bars_fixture(db_engine)
    ref = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )

    # tamper directly in the database (the verifier must catch it)
    async with db_engine.begin() as conn:
        await conn.execute(
            update(snapshots_t)
            .where(snapshots_t.c.id == ref.snapshot_id)
            .values(content='[{"close": 999.99}]')
        )

    checks = await verify_snapshot(db_engine, ref.snapshot_id)
    assert checks["ok"] is False
    assert checks["content_hash_matches"] is False

    with pytest.raises(SnapshotCorrupt):
        await read_snapshot(db_engine, ref.snapshot_id)


async def test_snapshot_pit_query_matches_frozen_content(db_engine):
    """The frozen content equals an independent PIT query at the same
    as_of — the snapshot adds immutability, not a second truth."""
    sec, ingest = await _bars_fixture(db_engine)
    ref = await freeze_daily_bars(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )
    snap = await read_snapshot(db_engine, ref.snapshot_id)
    bars = await daily_bars_asof(
        db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
        as_of=ingest.usable_at, mode="forward",
    )
    assert json.loads(snap["content"]) == bars


async def test_freeze_rejects_future_asof_and_bad_mode(db_engine):
    sec, _ = await _bars_fixture(db_engine)
    with pytest.raises(Exception, match="future"):
        await freeze_daily_bars(
            db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
            as_of=datetime.now(UTC) + __import__("datetime").timedelta(hours=1),
            mode="forward",
        )
    with pytest.raises(ValueError, match="mode"):
        await freeze_daily_bars(
            db_engine, [sec], date(2026, 9, 21), date(2026, 9, 25),
            as_of=datetime.now(UTC), mode="bogus",
        )
