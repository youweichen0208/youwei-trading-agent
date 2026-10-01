"""S06e: panel registration freeze/restore and the registration gate
(campaign-policy §2.1.1).

Acceptance:
- a new member's identity is valid FROM the frame as-of (the observed
  instant) with an explicit basis — never an invented 1990
- freeze records the complete mapping + frame + sample evidence,
  content-addressed and append-only; restore recreates the SAME
  permanent ids so a clean database re-derives the same frame and
  selection hashes
- the registration gate re-derives the frame from the source raw
  object + frozen mapping and rejects a wrong source, time, frame,
  selection or protocol/label — a dict-shape check alone is never
  enough
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from youwei_core.data.panel import (
    IDENTITY_BASIS,
    build_member_frame,
    draw_panel,
    freeze_panel_registration,
    restore_panel_registration,
    validate_panel_registration,
)
from youwei_core.data.securities import IdentitySpec
from youwei_core.data.eodhd import EODHDClient, ingest_sp500_constituents
from test_data_panel import _components

PROTOCOL_SHA = "a" * 64


def _client(body: str) -> EODHDClient:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return EODHDClient(
        "test-token",
        base_url="https://mock.test/api",
        min_interval=0.0,
        transport=httpx.MockTransport(handler),
    )


def _constituents_body(components):
    import json

    return json.dumps(
        {
            "General": {"Code": "GSPC", "Name": "S&P 500 Index", "Exchange": "INDX"},
            "Components": {
                str(i): {
                    "Code": c["ticker"],
                    "Exchange": "US",
                    "Name": c.get("name"),
                    "Sector": c["sector"],
                }
                for i, c in enumerate(components)
            },
        }
    )


async def _freeze(engine, *, frame_as_of=None, usable_at=None, stratification="eodhd_sector"):
    """Ingest the synthetic constituents, build the frame, draw and
    freeze a registration. Returns (registration, frame, sample)."""
    components = _components()
    client = _client(_constituents_body(components))
    ingest = await ingest_sp500_constituents(
        engine, client,
        source_available_at=usable_at,
    )
    frame_as_of = frame_as_of or ingest.usable_at.date()
    frame = await build_member_frame(engine, components, frame_as_of=frame_as_of)
    sample = draw_panel(frame, seed="20260927")
    reg = await freeze_panel_registration(
        engine,
        index="GSPC",
        stratification_level=stratification,
        protocol_ref="campaign-policy-v2",
        protocol_sha256=PROTOCOL_SHA,
        seed="20260927",
        sample_size=20,
        source_raw_object_id=ingest.raw_object_id,
        observed_at=ingest.usable_at,
        usable_at=ingest.usable_at,
        source_available_basis="caller_evidence",
        frame_as_of=frame_as_of,
        frame=frame,
        sample=sample,
    )
    await client.aclose()
    return reg, frame, sample


# --- identity validity ---------------------------------------------------------


async def test_new_member_identity_valid_from_frame_as_of(db_engine):
    components = _components()
    frame = await build_member_frame(db_engine, components, frame_as_of=date(2026, 9, 28))

    assert len(frame.identities) == 22
    for ident in frame.identities:
        assert ident["valid_from"] == "2026-09-28"  # not an invented 1990
        assert ident["basis"] == IDENTITY_BASIS

    # no identity is valid before the observed instant
    async with db_engine.begin() as conn:
        early = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM security_identities WHERE valid_from < '2026-09-28'"
                )
            )
        ).scalar_one()
    assert early == 0


# --- freeze / restore -----------------------------------------------------------


async def test_freeze_restore_reproduces_same_frame_hash(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    assert reg.created
    assert reg.frame_sha256 == frame.frame_sha256

    # wipe the securities master (simulate a clean database), restore,
    # and re-derive: the SAME permanent ids -> SAME frame hash
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text("TRUNCATE TABLE security_identities, securities RESTART IDENTITY CASCADE")
        )

    report = await restore_panel_registration(db_engine, reg.registration_id)
    assert len(report["restored"]) == 22
    assert report["mismatched"] == []

    components = _components()
    frame2 = await build_member_frame(
        db_engine, components, frame_as_of=reg.content["frame_as_of"]
    )
    assert frame2.frame_sha256 == frame.frame_sha256  # restored ids, not fresh randoms
    sample2 = draw_panel(frame2, seed="20260927")
    assert sample2.selected_list_sha256 == sample.selected_list_sha256

    # restore is idempotent
    again = await restore_panel_registration(db_engine, reg.registration_id)
    assert again["restored"] == []


async def test_restore_detects_ticker_mismatch(db_engine):
    from youwei_core.data.securities import IdentitySpec, create_security

    reg, frame, sample = await _freeze(db_engine)
    victim = frame.identities[0]

    # simulate a clean database
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text("TRUNCATE TABLE security_identities, securities RESTART IDENTITY CASCADE")
        )
    # a DIFFERENT permanent id already claims the frozen ticker
    await create_security(
        db_engine,
        identities=[IdentitySpec("ticker", victim["ticker"], date(2026, 9, 28))],
    )
    report = await restore_panel_registration(db_engine, reg.registration_id)
    assert victim["ticker"] in report["mismatched"]


# --- registration gate -----------------------------------------------------------


async def test_gate_accepts_consistent_registration(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    report = await validate_panel_registration(
        db_engine,
        reg.registration_id,
        panel_security_ids=sample.selected,
        protocol_sha256=PROTOCOL_SHA,
    )
    assert report["ok"], report["issues"]
    assert report["checks"]["frame_hash"]
    assert report["checks"]["selected_list_hash"]
    assert report["checks"]["panel_security_ids"]


async def test_gate_rejects_wrong_source(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    # corrupt the source raw object content
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE raw_objects SET content = content || ' ' WHERE id = :i"
            ),
            {"i": str(reg.content["source_raw_object_id"])},
        )
    report = await validate_panel_registration(db_engine, reg.registration_id)
    assert not report["ok"]
    assert any("content hash" in i for i in report["issues"])


async def test_gate_rejects_wrong_time(db_engine):
    # frame_as_of predates the source's usable_at -> future data claimed
    reg, frame, sample = await _freeze(
        db_engine, frame_as_of=date(2026, 8, 1), usable_at=datetime.now(UTC)
    )
    report = await validate_panel_registration(db_engine, reg.registration_id)
    assert not report["ok"]
    assert any("future data" in i for i in report["issues"])


async def test_gate_rejects_wrong_frame(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    # re-freeze is content-addressed; mutate the stored frame hash
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text("UPDATE panel_registrations SET frame_sha256 = :h WHERE id = :i"),
            {"h": "0" * 64, "i": str(reg.registration_id)},
        )
    report = await validate_panel_registration(db_engine, reg.registration_id)
    assert not report["ok"]
    assert any("frame hash" in i for i in report["issues"])


async def test_gate_rejects_wrong_selection(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    wrong = list(sample.selected)
    wrong[0] = str(uuid.uuid4())
    report = await validate_panel_registration(
        db_engine, reg.registration_id, panel_security_ids=wrong
    )
    assert not report["ok"]
    assert any("panel_security_ids" in i for i in report["issues"])


async def test_gate_rejects_wrong_protocol_label(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    report = await validate_panel_registration(
        db_engine, reg.registration_id, protocol_sha256="b" * 64
    )
    assert not report["ok"]
    assert any("protocol hash" in i for i in report["issues"])


async def test_registration_append_only(db_engine):
    reg, frame, sample = await _freeze(db_engine)
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM panel_registrations"))


# --- register_campaign gate --------------------------------------------------------


async def test_campaign_gate_requires_validating_registration(db_engine, tenant_id):
    from youwei_core.ledger.service import CampaignValidationError, register_campaign
    from youwei_core.data.securities import create_security
    from test_ledger_campaign import TIME_SHA, _release, _specs

    reg, frame, sample = await _freeze(db_engine)
    from youwei_core.auth.service import create_tenant

    await create_tenant(db_engine, f"tenant-{tenant_id}", tenant_id=tenant_id)
    benchmark = await create_security(
        db_engine,
        asset_class="etf",
        name="SPY",
        identities=[IdentitySpec("ticker", "SPY", date(1990, 1, 1))],
    )
    await _release(db_engine)

    async def register(panel_ids, reg_id, key):
        return await register_campaign(
            db_engine,
            tenant_id=tenant_id,
            campaign_key=key,
            release_id="rel-test-v1",
            target_specs=_specs(),
            time_protocol_ref="time-protocol-v1",
            time_protocol_sha256=TIME_SHA,
            benchmark_security_id=benchmark,
            panel_security_ids=panel_ids,
            panel_manifest={"panel_registration_id": str(reg_id)},
            enabled_sources=["baseline", "quant_model"],
        )

    # a consistent registration passes
    rec = await register(sample.selected, reg.registration_id, "c-ok")
    assert rec.created

    # wrong panel_security_ids are rejected
    wrong = list(sample.selected)
    wrong[0] = str(uuid.uuid4())
    with pytest.raises(CampaignValidationError, match="panel_security_ids"):
        await register(wrong, reg.registration_id, "c-wrong")

    # a bogus registration id is rejected
    with pytest.raises(CampaignValidationError, match="not a valid frozen"):
        await register(sample.selected, uuid.uuid4(), "c-bogus")
