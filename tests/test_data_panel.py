"""Panel construction (S06, campaign-policy §2): ticker -> permanent
security_id mapping, frame normalization, and the registered draw."""

from datetime import date

import pytest
from sqlalchemy import text

from youwei_core.data.panel import (
    build_member_frame,
    build_panel_manifest,
    canonical_ticker,
    draw_panel,
)
from youwei_core.data.securities import IdentitySpec, create_security


def _components():
    """22 members over 11 sectors (two per sector) — enough for the
    N=20 draw and mirroring the K01 frame shape."""
    members = []
    sectors = [
        "Technology", "Financial Services", "Healthcare", "Industrials",
        "Consumer Cyclical", "Consumer Defensive", "Communication Services",
        "Energy", "Utilities", "Real Estate", "Basic Materials",
    ]
    for i, sector in enumerate(sectors, start=1):
        members.append({"ticker": f"T{i:02d}A", "name": f"Firm {i}A", "sector": sector})
        members.append({"ticker": f"T{i:02d}B", "name": f"Firm {i}B", "sector": sector})
    return members


def test_canonical_ticker_normalizes_vendor_forms():
    assert canonical_ticker("brk-b") == "BRK.B"
    assert canonical_ticker("BF-B") == "BF.B"
    assert canonical_ticker("AAPL") == "AAPL"


async def test_build_member_frame_maps_and_creates_securities(db_engine):
    components = _components()
    # one member already exists in the master under the price-vendor ticker
    existing = await create_security(
        db_engine,
        name="Existing",
        identities=[IdentitySpec("ticker", "T01A", date(1990, 1, 1))],
    )

    frame = await build_member_frame(db_engine, components, frame_as_of=date(2026, 9, 28))
    assert len(frame.rows) == 22
    assert frame.mapping["T01A"] == str(existing)  # resolved, not re-created
    assert frame.mapping["T01B"] != str(existing)  # new member created
    assert len(frame.sector_counts) == 11
    assert set(frame.sector_counts.values()) == {2}

    # the normalized frame is the two protocol fields only, sorted by id
    for row in frame.rows:
        assert set(row) == {"security_id", "sector_code"}
    ids = [r["security_id"] for r in frame.rows]
    assert ids == sorted(ids)

    # a duplicate build resolves to the same security ids (idempotent)
    again = await build_member_frame(db_engine, components, frame_as_of=date(2026, 9, 28))
    assert again.frame_sha256 == frame.frame_sha256
    assert {r["security_id"] for r in again.rows} == {r["security_id"] for r in frame.rows}

    async with db_engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM securities"))).scalar_one()
    assert n == 22  # existing + 21 created, no duplicates


async def test_build_member_frame_stops_without_sector(db_engine):
    components = _components()
    components[0]["sector"] = ""
    with pytest.raises(ValueError, match="no sector"):
        await build_member_frame(db_engine, components, frame_as_of=date(2026, 9, 28))


async def test_draw_and_manifest(db_engine):
    frame = await build_member_frame(db_engine, _components(), frame_as_of=date(2026, 9, 28))
    sample = draw_panel(frame, seed="20260927")
    assert len(sample.selected) == 20
    assert sample.frame_sha256 == frame.frame_sha256

    manifest = build_panel_manifest(
        frame,
        sample,
        index="GSPC",
        frame_as_of=date(2026, 9, 28),
        raw_object_id="00000000-0000-0000-0000-000000000000",
        source_version="spglobal-constituents",
        stratification_level="eodhd_sector",  # the protocol label is the caller's decision
    )
    assert manifest["stratification_level"] == "eodhd_sector"
    assert manifest["frame_as_of"] == "2026-09-28"
    assert manifest["index"] == "GSPC"
    assert manifest["source"]["vendor"] == "EODHD"
    assert manifest["source"]["raw_object_id"] == "00000000-0000-0000-0000-000000000000"
    assert manifest["selected_list_sha256"] == sample.selected_list_sha256
    assert manifest["selected"] == sample.selected
    assert len(manifest["ticker_mapping"]) == 22
