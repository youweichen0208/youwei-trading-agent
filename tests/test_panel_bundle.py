"""Portable panel recovery through two independently migrated databases."""

import json
import hashlib
import os
import subprocess
import sys
import uuid
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest_asyncio
import pytest
from sqlalchemy.engine import make_url

from test_data_panel_registration import _freeze
from youwei_core.data.panel import validate_panel_registration
from youwei_core.db.engine import make_engine


@pytest_asyncio.fixture
async def recovery_engine(pg_url):
    # pg_url is exclusively supplied by the disposable Docker fixture.
    name = "panel_restore_" + uuid.uuid4().hex
    admin = make_engine(pg_url)
    url = make_url(pg_url).set(database=name).render_as_string(hide_password=False)
    async with admin.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql(f"CREATE DATABASE {name}")
    engine = make_engine(url)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "YOUWEI_DATABASE_URL": url},
            check=True, capture_output=True,
        )
        yield engine
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        await admin.dispose()


async def test_bundle_restores_full_evidence_and_original_ids_in_a_new_database(db_engine, recovery_engine):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle

    registration, frame, sample = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    bundle = await export_registration_bundle(db_engine, registration.registration_id)
    # A file/transport round trip must not depend on live ORM/UUID objects.
    transported = json.loads(json.dumps(bundle))
    restored = await restore_registration_bundle(recovery_engine, transported)
    assert restored["registration_id"] == str(registration.registration_id)
    assert restored["created"] is True
    assert restored["validation"]["ok"] is True
    report = await validate_panel_registration(
        recovery_engine, registration.registration_id,
        panel_security_ids=sample.selected, protocol_sha256="a" * 64,
    )
    assert report["ok"] is True
    recovered = await export_registration_bundle(recovery_engine, registration.registration_id)
    assert recovered == bundle
    assert recovered["payload"]["registration"]["frame_sha256"] == frame.frame_sha256
    assert recovered["payload"]["registration"]["selected_list_sha256"] == sample.selected_list_sha256


@pytest.mark.parametrize("part", ["raw_content", "mapping", "source_time", "version"])
async def test_changed_bundle_is_rejected_before_any_registration_is_imported(db_engine, recovery_engine, part):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle

    registration, _, _ = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    bundle = deepcopy(await export_registration_bundle(db_engine, registration.registration_id))
    if part == "raw_content":
        bundle["payload"]["raw_object"]["content"] += " "
    elif part == "mapping":
        bundle["payload"]["registration"]["mapping"][0]["security_id"] = str(uuid.uuid4())
    elif part == "source_time":
        bundle["payload"]["raw_object"]["usable_at"] = "1990-01-01T00:00:00+00:00"
    else:
        bundle["bundle_version"] = "unknown-version"
    with pytest.raises(ValueError, match="bundle"):
        await restore_registration_bundle(recovery_engine, bundle)
    with pytest.raises(ValueError, match="not found"):
        await export_registration_bundle(recovery_engine, registration.registration_id)


async def test_restoring_the_same_bundle_twice_is_idempotent(db_engine, recovery_engine):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle

    registration, _, _ = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    bundle = await export_registration_bundle(db_engine, registration.registration_id)
    await restore_registration_bundle(recovery_engine, bundle)
    second = await restore_registration_bundle(recovery_engine, bundle)
    assert second["created"] is False
    assert second["validation"]["ok"] is True
    assert await export_registration_bundle(recovery_engine, registration.registration_id) == bundle


async def test_recovery_rejects_an_overlapping_ticker_owned_by_another_security(db_engine, recovery_engine):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle
    from youwei_core.data.securities import IdentitySpec, create_security, resolve_identifier

    registration, _, _ = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    bundle = await export_registration_bundle(db_engine, registration.registration_id)
    member = bundle["payload"]["registration"]["mapping"][0]
    as_of = date.fromisoformat(member["valid_from"])
    other_id = await create_security(recovery_engine, identities=[
        IdentitySpec("ticker", member["ticker"], as_of - timedelta(days=1))
    ])
    with pytest.raises(ValueError, match="conflict"):
        await restore_registration_bundle(recovery_engine, bundle)
    assert await resolve_identifier(recovery_engine, "ticker", member["ticker"], as_of) == other_id
    with pytest.raises(ValueError, match="not found"):
        await export_registration_bundle(recovery_engine, registration.registration_id)


async def test_failed_internal_validation_rolls_back_the_whole_import(db_engine, recovery_engine):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle

    registration, _, _ = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    original = await export_registration_bundle(db_engine, registration.registration_id)
    broken = deepcopy(original)
    broken["payload"]["registration"]["frame_sha256"] = "f" * 64
    # A valid transport digest cannot replace checking the source-to-panel linkage.
    broken["content_sha256"] = hashlib.sha256(json.dumps(
        broken["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    with pytest.raises(ValueError, match="valid"):
        await restore_registration_bundle(recovery_engine, broken)
    result = await restore_registration_bundle(recovery_engine, original)
    assert result["created"] is True
    assert result["validation"]["ok"] is True


async def test_incomplete_identity_bundle_cannot_claim_successful_recovery(db_engine, recovery_engine):
    from youwei_core.data.panel_bundle import export_registration_bundle, restore_registration_bundle

    registration, _, _ = await _freeze(db_engine, frame_as_of=datetime.now(UTC).date())
    original = await export_registration_bundle(db_engine, registration.registration_id)
    incomplete = deepcopy(original)
    incomplete["payload"]["security_identities"].pop()
    incomplete["content_sha256"] = hashlib.sha256(json.dumps(
        incomplete["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    with pytest.raises(ValueError, match="identit"):
        await restore_registration_bundle(recovery_engine, incomplete)
    assert (await restore_registration_bundle(recovery_engine, original))["created"] is True
