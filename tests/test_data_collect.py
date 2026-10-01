"""Tests for the S09a daily collection scheduler (collect_tick).

Covers the pure slot computation and the DB-level target resolution
and submission idempotency. The actual Tiingo fetch is exercised by
the existing data.tiingo_daily handler tests with a mock transport;
these tests inject a mock client or assert job submission only.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from youwei_core.data.calendar import ET
from youwei_core.data.collect import (
    CollectionConfigError,
    CollectionTarget,
    collect_tick,
    observation_slot,
    resolve_collection_targets,
)
from youwei_core.data.securities import IdentitySpec, create_security
from youwei_core.data.tiingo import TiingoClient
from youwei_core.db.meta import (
    data_sources,
    panel_registrations,
    raw_objects,
    research_releases,
    runs,
)
from youwei_core.ledger.service import register_release


# --- pure: observation_slot ---------------------------------------------


@pytest.mark.parametrize(
    "local_iso, expected",
    [
        # First collection: 17:30 ET onward.
        ("2026-10-10T17:30:00-04:00", "2026-10-10T17:30"),
        ("2026-10-10T17:44:00-04:00", "2026-10-10T17:30"),
        ("2026-10-10T18:00:00-04:00", "2026-10-10T18:00"),
        ("2026-10-10T22:30:00-04:00", "2026-10-10T22:30"),
        # Past midnight follow-up (next day, EST): 05:30 ET cutoff.
        ("2026-10-11T02:00:00-04:00", "2026-10-11T02:00"),
        # Winter (EST): 17:30 ET = 22:30 UTC; slot string stays local.
        ("2026-12-12T17:30:00-05:00", "2026-12-12T17:30"),
        # Before 17:30, before 05:30 follow-up: still open (prev-day slot).
        ("2026-10-11T04:30:00-04:00", "2026-10-11T04:30"),
        # Between 05:30 and 17:30: no active slot.
        ("2026-10-10T10:00:00-04:00", ""),
    ],
)
def test_observation_slot(local_iso, expected):
    dt = datetime.fromisoformat(local_iso)
    assert observation_slot(dt) == expected


def test_observation_slot_dst_boundary_keeps_local_wall_clock():
    # 17:30 ET in EDT is 21:30 UTC; in EST it is 22:30 UTC. The slot
    # string must reflect the LOCAL wall clock, not UTC.
    edt = datetime(2026, 10, 10, 17, 30, tzinfo=ET)
    est = datetime(2026, 12, 12, 17, 30, tzinfo=ET)
    assert observation_slot(edt) == "2026-10-10T17:30"
    assert observation_slot(est) == "2026-12-12T17:30"
    assert edt.astimezone(UTC).hour == 21  # EDT -> 21:30 UTC
    assert est.astimezone(UTC).hour == 22  # EST -> 22:30 UTC


# --- DB: resolve_collection_targets -------------------------------------


async def _make_release(engine, release_id, panel_id, benchmark_id):
    manifest = {
        "enabled_sources": ["baseline", "quant_model"],
        "fallback_policy": "phase1a-none",
        "panel_registration_id": str(panel_id),
        "references": {
            "benchmark_security_id": str(benchmark_id),
            "benchmark_ticker": "SPY",
        },
    }
    await register_release(engine, release_id=release_id, manifest=manifest)


async def _make_panel(engine, panel_id, selected_ids):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # A minimal raw object satisfies the FK for source_raw_object_id.
    # Content hashes derive from the panel id so each panel gets its own
    # raw object (uq_raw_objects_content is (source, endpoint, query,
    # content)).
    raw_id = uuid.uuid4()
    query_sha = hashlib.sha256(f"q:{panel_id}".encode()).hexdigest()
    content_sha = hashlib.sha256(f"c:{panel_id}".encode()).hexdigest()
    frame_sha = hashlib.sha256(f"frame:{panel_id}".encode()).hexdigest()
    map_sha = hashlib.sha256(f"map:{panel_id}".encode()).hexdigest()
    sel_sha = hashlib.sha256(f"sel:{panel_id}".encode()).hexdigest()
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(data_sources)
            .values(id="test-source", name="test", vendor_version="1",
                    license_tags=[], config={})
            .on_conflict_do_nothing(index_elements=[data_sources.c.id])
        )
        await conn.execute(
            pg_insert(raw_objects)
            .values(
                id=raw_id, source_id="test-source", endpoint="test",
                query={}, query_sha256=query_sha, content="{}",
                content_sha256=content_sha, content_type="application/json",
                row_count=0, parser_version="v1",
                source_available_at=None, source_available_basis="test",
                usable_at=datetime.now(UTC),
            )
        )
        await conn.execute(
            pg_insert(panel_registrations)
            .values(
                id=panel_id,
                index="GSPC",
                stratification_level="eodhd_sector",
                protocol_ref="campaign-policy-v2",
                protocol_sha256="a" * 64,
                seed="20260927",
                sample_size=len(selected_ids),
                source_raw_object_id=raw_id,
                observed_at=None,
                usable_at=datetime.now(UTC),
                source_available_basis="test",
                frame_as_of=date(2026, 10, 1),
                frame_sha256=frame_sha,
                mapping=[{"ticker": f"S{i}", "exchange": "US",
                          "security_id": str(sid), "valid_from": "2026-10-01",
                          "basis": "test"}
                         for i, sid in enumerate(selected_ids)],
                mapping_sha256=map_sha,
                quotas={},
                selected=[str(s) for s in selected_ids],
                selected_list_sha256=sel_sha,
            )
            .on_conflict_do_nothing(index_elements=[panel_registrations.c.id])
        )


async def _sec_with_ticker(engine, ticker):
    return await create_security(
        engine, identities=[IdentitySpec("ticker", ticker, date(1990, 1, 1))]
    )


async def test_resolve_targets_reads_named_release_not_latest(db_engine):
    a = await _sec_with_ticker(db_engine, "AAA")
    b = await _sec_with_ticker(db_engine, "BBB")
    bench = await _sec_with_ticker(db_engine, "SPY")
    panel1 = uuid.uuid4()
    await _make_panel(db_engine, panel1, [a])
    await _make_release(db_engine, "rel-1", panel1, bench)

    # A second, newer release/panel must NOT change rel-1's set.
    c = await _sec_with_ticker(db_engine, "CCC")
    panel2 = uuid.uuid4()
    await _make_panel(db_engine, panel2, [c])
    await _make_release(db_engine, "rel-2", panel2, bench)

    targets = await resolve_collection_targets(db_engine, "rel-1")
    tickers = sorted(t.ticker for t in targets)
    assert tickers == ["AAA", "SPY"]


async def test_resolve_targets_dedups_benchmark_when_in_panel(db_engine):
    a = await _sec_with_ticker(db_engine, "AAA")
    bench = await _sec_with_ticker(db_engine, "SPY")
    panel = uuid.uuid4()
    # benchmark also in the selected list -> must be de-duplicated.
    await _make_panel(db_engine, panel, [a, bench])
    await _make_release(db_engine, "rel-1", panel, bench)

    targets = await resolve_collection_targets(db_engine, "rel-1")
    tickers = sorted(t.ticker for t in targets)
    assert tickers == ["AAA", "SPY"]
    assert len(targets) == 2


async def test_resolve_targets_missing_release_raises(db_engine):
    with pytest.raises(CollectionConfigError):
        await resolve_collection_targets(db_engine, "does-not-exist")


async def test_resolve_targets_missing_ticker_raises(db_engine):
    a = await _sec_with_ticker(db_engine, "AAA")
    # a security with NO ticker identity
    from youwei_core.data.securities import create_security as _cs
    no_ticker = await _cs(db_engine)  # no identities
    bench = await _sec_with_ticker(db_engine, "SPY")
    panel = uuid.uuid4()
    await _make_panel(db_engine, panel, [a, no_ticker])
    await _make_release(db_engine, "rel-1", panel, bench)
    with pytest.raises(CollectionConfigError, match="no ticker"):
        await resolve_collection_targets(db_engine, "rel-1")


# --- DB: collect_tick submission idempotency ----------------------------


class _FakeTiingo(TiingoClient):
    """Never actually called by collect_tick (it only schedules jobs)."""

    def __init__(self):
        super().__init__("unused")


async def test_collect_tick_submits_and_is_idempotent(db_engine, tenant_id):
    a = await _sec_with_ticker(db_engine, "AAA")
    bench = await _sec_with_ticker(db_engine, "SPY")
    panel = uuid.uuid4()
    await _make_panel(db_engine, panel, [a])
    await _make_release(db_engine, "rel-1", panel, bench)

    from youwei_core.auth.service import create_tenant
    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)

    client = _FakeTiingo()
    now = datetime(2026, 10, 10, 21, 30, tzinfo=UTC)  # 17:30 ET
    # ensure calendar covers 2026
    from youwei_core.data.calendar import build_calendar
    await build_calendar(db_engine, year_start=2026, year_end=2026)

    first = await collect_tick(
        db_engine, client, release_id="rel-1", tenant_id=tenant_id, db_now=now
    )
    assert first["targets"] == 2
    assert len(first["submitted"]) == 2
    assert first["idempotent"] == []

    second = await collect_tick(
        db_engine, client, release_id="rel-1", tenant_id=tenant_id, db_now=now
    )
    assert second["submitted"] == []
    assert len(second["idempotent"]) == 2
