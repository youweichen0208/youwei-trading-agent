"""Release-bound numeric models execute through the real batch/sealing path."""

from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import text

from quant.dataset import FEATURE_NAMES
from quant.logistic import LogisticSpec, MODEL_VERSION, export_model, train_model
from youwei_core.data.calendar import next_weekly_cutoff, session_times
from youwei_core.ledger.service import plan_batch, sha256_hex
from youwei_core.ledger.model_registry import ModelRegistryError
from test_data_pit import _security
from test_ledger_campaign import _build_calendar, _campaign, _release
from test_ledger_outcomes import _ingest_window
from test_ledger_pipeline import _open_batch, _run_predict_job


def _model_manifest():
    artifacts = {}
    for horizon in (1, 20, 60):
        model = train_model(
            [[-2.0, -2.0, 0.0], [-1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 2.0, 0.0]],
            [-0.2, -0.1, 0.1, 0.2],
            label_available_at=[datetime(2022, 1, 1, tzinfo=UTC)] * 4,
            training_cutoff=datetime(2023, 1, 1, tzinfo=UTC),
            spec=LogisticSpec(horizon_td=horizon, feature_names=FEATURE_NAMES),
        )
        payload = export_model(model)
        # Synthetic known answer: fixed 0.5 probability, horizon/1000 return.
        payload["logistic"]["coef"] = [0.0] * 3
        payload["logistic"]["intercept"] = 0.0
        payload["expected_return"]["coef"] = [0.0] * 3
        payload["expected_return"]["intercept"] = horizon / 1000
        artifacts[str(horizon)] = {"content": payload, "content_sha256": sha256_hex(payload)}
    return {
        "enabled_sources": ["baseline", "quant_model"],
        "fallback_policy": "phase1a-none",
        "baseline_version": "baseline-constant-v0",
        "quant_model_version": MODEL_VERSION,
        "quant_artifacts": artifacts,
    }


async def _setup_model_batch(engine, tenant_id, *, manifest=None, with_bars=True):
    await _build_calendar(engine)
    benchmark = await _security(engine, ticker="SPY")
    stock = await _security(engine, ticker="S0")
    await _release(engine, manifest=manifest or _model_manifest())
    campaign = await _campaign(engine, tenant_id, benchmark, [stock])
    plan = await plan_batch(
        engine, campaign.campaign_id, decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    if with_bars:
        now = datetime.now(UTC)
        async with engine.begin() as conn:
            dates = (await conn.execute(text(
                "SELECT date FROM calendar_days WHERE is_trading AND date <= :today ORDER BY date DESC LIMIT 70"
            ), {"today": now.date()})).scalars().all()
        dates = sorted([day for day in dates if (await session_times(engine, day)).close_utc <= now])[-65:]
        await _ingest_window(engine, "S0", stock, dates)
        await _ingest_window(engine, "SPY", benchmark, dates)
    await _open_batch(engine, plan.batch_id, plan.case_ids)
    return plan


async def test_batch_uses_frozen_release_model_for_each_horizon(db_engine, tenant_id):
    plan = await _setup_model_batch(db_engine, tenant_id)
    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 3
    assert summary["failed"] == []
    async with db_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT p.model_version, p.source_status, p.p_outperform, p.expected_excess_return, "
            "p.evidence_snapshot_id, fc.horizon_td FROM predictions p "
            "JOIN forecast_commits c ON c.id = p.commit_id JOIN forecast_cases fc ON fc.id = c.case_id "
            "WHERE p.source = 'quant_model' ORDER BY fc.horizon_td"
        ))).mappings().all()
    assert [row.model_version for row in rows] == [MODEL_VERSION] * 3
    assert [float(row.expected_excess_return) for row in rows] == [0.001, 0.02, 0.06]
    assert all(row.source_status == "produced" and row.p_outperform == 0.5 for row in rows)
    assert len({row.evidence_snapshot_id for row in rows}) == 1


@pytest.mark.parametrize("fault", ["hash", "version", "horizon", "feature_names", "future_fit"])
async def test_invalid_release_model_fails_before_freeze_or_sealing(db_engine, tenant_id, fault):
    manifest = _model_manifest()
    envelope = manifest["quant_artifacts"]["20"]
    if fault == "hash":
        envelope["content_sha256"] = "0" * 64
    elif fault == "version":
        manifest["quant_model_version"] = "unknown-model-v9"
    elif fault == "horizon":
        envelope["content"]["spec"]["horizon_td"] = 1
    elif fault == "feature_names":
        envelope["content"]["spec"]["feature_names"][0] = "future_earnings"
    else:
        envelope["content"]["training"]["cutoff"] = "2099-01-01T00:00:00+00:00"
    if fault != "hash":
        envelope["content_sha256"] = sha256_hex(envelope["content"])
    plan = await _setup_model_batch(db_engine, tenant_id, manifest=manifest, with_bars=False)
    with pytest.raises(ModelRegistryError):
        await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    async with db_engine.begin() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM forecast_commits"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM snapshots"))).scalar_one() == 0


@pytest.mark.parametrize("field,value", [("calendar_sha256", "0" * 64), ("tzdb_version", "unknown")])
async def test_frozen_calendar_hash_and_timezone_must_match_before_prediction(db_engine, tenant_id, field, value):
    plan = await _setup_model_batch(db_engine, tenant_id, with_bars=False)
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(text(
            "UPDATE forecast_batches SET batch_manifest = batch_manifest || CAST(:patch AS jsonb) WHERE id = :id"
        ), {"patch": json.dumps({field: value}), "id": plan.batch_id})
    with pytest.raises(ModelRegistryError):
        await _run_predict_job(db_engine, tenant_id, plan.batch_id)


async def test_missing_frozen_features_seal_unavailable_without_vehicle_fallback(db_engine, tenant_id):
    plan = await _setup_model_batch(db_engine, tenant_id, with_bars=False)
    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 3
    async with db_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT model_version, source_status, reason, evidence_snapshot_id FROM predictions WHERE source='quant_model'"
        ))).mappings().all()
    assert len(rows) == 3
    assert all(row.model_version == MODEL_VERSION and row.source_status == "unavailable" for row in rows)
    assert all(row.reason == "incomplete_frozen_features" and row.evidence_snapshot_id for row in rows)


async def test_materialized_calendar_changes_do_not_change_frozen_batch_inputs(db_engine, tenant_id):
    plan = await _setup_model_batch(db_engine, tenant_id)
    # The index is rebuildable; only the batch-referenced calendar build is authoritative.
    async with db_engine.begin() as conn:
        await conn.execute(text("UPDATE calendar_days SET is_trading=false, early_close=false"))
    summary, _ = await _run_predict_job(db_engine, tenant_id, plan.batch_id)
    assert summary["sealed"] == 3
    async with db_engine.begin() as conn:
        statuses = (await conn.execute(text(
            "SELECT source_status FROM predictions WHERE source='quant_model'"
        ))).scalars().all()
    assert statuses == ["produced"] * 3
