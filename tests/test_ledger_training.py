"""S04: training manifest registration (campaign-policy §5).

Acceptance:
- registration is idempotent and immutable: same id + same content
  returns the existing row; different content conflicts; the table is
  append-only like the rest of the ledger
- the manifest structure is validated: models and feature sets with
  explicit preprocessing / label maturation / fitting window /
  calibration / artifacts; "none" must be stated, never silently
  defaulted; a model's feature set must resolve
- the vehicle manifest is a known answer: its artifact hash pins the
  actual quant module bytes (changing the code without a new
  registration is detectable) and its content hash is deterministic
  and self-excluding
- campaign registration enforces the reference: a release manifest's
  training_manifest_ref + sha256 must resolve to registered content,
  and the declared feature set / model versions must be consistent;
  releases without the reference register as before
"""

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

import quant.models as quant_module
from conftest import make_campaign_plan
from youwei_core.db.meta import research_releases
from youwei_core.ledger.service import (
    CampaignValidationError,
    approve_release,
    register_campaign,
)
from youwei_core.ledger.training import (
    ManifestValidationError,
    TrainingManifestConflict,
    quant_module_artifact,
    register_training_manifest,
    training_manifest_content_hash,
    vehicle_training_manifest,
)
from test_ledger_campaign import TIME_SHA, _release, _setup, _specs

VEHICLE_ID = "tm-vehicles-v0"


async def _campaign_for(engine, tenant_id, ctx, release_id, key=None):
    """register_campaign with a fresh key (the shared helper pins one)."""
    campaign_key = key or f"c-tm-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        release_sha = (
            await conn.execute(
                select(research_releases.c.release_content_sha256).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).scalar_one()
    plan, scope = make_campaign_plan(
        tenant_id=tenant_id,
        campaign_key=campaign_key,
        release_content_sha256=release_sha,
        benchmark_security_id=ctx["benchmark"],
        panel_security_ids=[str(s) for s in ctx["panel"]],
        target_specs=_specs(),
        time_protocol_sha256=TIME_SHA,
    )
    await approve_release(
        engine,
        release_id=release_id,
        approver_principal_id="human-owner",
        scope="phase1a-forward",
        scope_manifest=scope.model_dump(mode="json"),
    )
    return await register_campaign(
        engine,
        tenant_id=tenant_id,
        campaign_key=campaign_key,
        release_id=release_id,
        target_specs=_specs(),
        time_protocol_ref="time-protocol-v1",
        time_protocol_sha256=TIME_SHA,
        benchmark_security_id=ctx["benchmark"],
        panel_security_ids=[str(s) for s in ctx["panel"]],
        panel_manifest={"sampler_version": "sector-stratified-hash-v1"},
        enabled_sources=["baseline", "quant_model"],
        planned_cutoffs=plan["planned_cutoffs"],
        planned_cutoffs_sha256=plan["planned_cutoffs_sha256"],
        campaign_plan_sha256=plan["campaign_plan_sha256"],
    )


# --- registration ------------------------------------------------------------------


async def test_registration_idempotent_and_conflict(db_engine):
    m = vehicle_training_manifest()
    a = await register_training_manifest(db_engine, manifest=m)
    b = await register_training_manifest(db_engine, manifest=m)
    assert a.created and not b.created
    assert a.training_manifest_row_id == b.training_manifest_row_id
    assert a.content_sha256 == b.content_sha256

    changed = vehicle_training_manifest()
    changed["description"] = "silently changed"
    with pytest.raises(TrainingManifestConflict):
        await register_training_manifest(db_engine, manifest=changed)

    async with db_engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM training_manifests"))
        ).scalar_one()
    assert n == 1


async def test_manifest_structure_validation(db_engine):
    cases = []
    m = vehicle_training_manifest(); del m["manifest_id"]; cases.append(m)
    m = vehicle_training_manifest(); m["models"] = []; cases.append(m)
    m = vehicle_training_manifest(); m["feature_sets"] = []; cases.append(m)
    m = vehicle_training_manifest(); del m["models"][0]["calibration"]; cases.append(m)
    m = vehicle_training_manifest(); del m["models"][1]["label_maturation"]; cases.append(m)
    m = vehicle_training_manifest(); m["models"][0]["artifact"]["sha256"] = "xyz"; cases.append(m)
    m = vehicle_training_manifest(); m["models"][0]["fitting_window"] = "   "; cases.append(m)
    m = vehicle_training_manifest(); m["models"][1]["feature_set"] = "unknown-fs"; cases.append(m)
    m = vehicle_training_manifest(); m["feature_sets"][0]["features"] = []; cases.append(m)
    m = vehicle_training_manifest()
    del m["feature_sets"][0]["features"][0]["missing_policy"]
    cases.append(m)
    m = vehicle_training_manifest()
    m["feature_sets"].append(dict(m["feature_sets"][0]))
    cases.append(m)  # duplicate feature_set_version

    for broken in cases:
        with pytest.raises(ManifestValidationError):
            await register_training_manifest(db_engine, manifest=broken)

    async with db_engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM training_manifests"))
        ).scalar_one()
    assert n == 0  # nothing invalid ever lands


async def test_content_hash_deterministic_and_self_excluding():
    m = vehicle_training_manifest()
    h1 = training_manifest_content_hash(m)
    with_hash = dict(m)
    with_hash["content_sha256"] = h1
    assert training_manifest_content_hash(with_hash) == h1  # self-excluding
    reordered = {k: m[k] for k in reversed(list(m))}
    assert training_manifest_content_hash(reordered) == h1  # key order irrelevant


# --- the vehicle manifest (known answer) ---------------------------------------------


async def test_vehicle_manifest_known_answer(db_engine):
    """The vehicles' artifact hash pins the actual quant module bytes,
    and the declared versions match the running code."""
    m = vehicle_training_manifest()
    actual = hashlib.sha256(Path(quant_module.__file__).read_bytes()).hexdigest()
    assert quant_module_artifact()["sha256"] == actual
    roles = {model["role"]: model for model in m["models"]}
    assert roles["baseline"]["model_version"] == quant_module.BASELINE_MODEL_VERSION
    assert roles["quant_model"]["model_version"] == quant_module.QUANT_MODEL_VERSION
    for model in m["models"]:
        assert model["artifact"]["sha256"] == actual
        # the protocol's five fixed facts are stated explicitly —
        # "none" is a declared value, never a silent default
        for key in ("preprocessing", "fitting_window", "calibration", "label_maturation"):
            assert model[key].strip()
    assert vehicle_training_manifest() == m  # deterministic

    rec = await register_training_manifest(db_engine, manifest=m)
    assert rec.created and rec.manifest_id == VEHICLE_ID


async def test_training_manifests_append_only(db_engine):
    await register_training_manifest(db_engine, manifest=vehicle_training_manifest())
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM training_manifests"))


# --- campaign wiring ------------------------------------------------------------------


async def test_campaign_enforces_training_reference(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id)

    # releases without a training reference register as before
    assert (
        await _campaign_for(db_engine, tenant_id, ctx, "rel-test-v1")
    ).created

    # a release referencing the registered vehicle manifest registers
    tm = await register_training_manifest(
        db_engine, manifest=vehicle_training_manifest()
    )
    manifest = {
        "enabled_sources": ["baseline", "quant_model"],
        "fallback_policy": "phase1a-none",
        "prompt_version": "not_enabled",
        "training_manifest_ref": VEHICLE_ID,
        "training_manifest_sha256": tm.content_sha256,
        "feature_set_version": "momentum-20d-v0",
        "quant_model_version": quant_module.QUANT_MODEL_VERSION,
    }
    await _release(db_engine, release_id="rel-with-tm", manifest=manifest)
    assert (
        await _campaign_for(db_engine, tenant_id, ctx, "rel-with-tm")
    ).created

    async def rejected(release_id, rel_manifest, match):
        await _release(db_engine, release_id=release_id, manifest=rel_manifest)
        with pytest.raises(CampaignValidationError, match=match):
            await _campaign_for(db_engine, tenant_id, ctx, release_id)

    await rejected(
        "rel-unknown-tm",
        dict(manifest, training_manifest_ref="tm-missing"),
        "not registered",
    )
    await rejected(
        "rel-hash-mismatch",
        dict(manifest, training_manifest_sha256="0" * 64),
        "content hash mismatch",
    )
    await rejected(
        "rel-undeclared-fs",
        dict(manifest, feature_set_version="other-fs-v9"),
        "not declared",
    )
    await rejected(
        "rel-wrong-model",
        dict(manifest, quant_model_version="quant-other-v9"),
        "does not match",
    )
    no_hash = {k: v for k, v in manifest.items() if k != "training_manifest_sha256"}
    await rejected("rel-no-hash", no_hash, "requires training_manifest_sha256")
