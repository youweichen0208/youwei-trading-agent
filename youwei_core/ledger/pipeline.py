"""Forward prediction pipeline (S06 vehicle).

The Controller-side job that turns a planned batch into sealed
predictions:

1. freeze ONE evidence snapshot for the whole batch at the batch's
   decision cutoff (PIT forward view: only data usable at the cutoff;
   architecture §3.1 — one manifest shared by the batch's research)
2. run the registered model implementations over the frozen bars
3. seal each case's three positions atomically (Phase 1A: llm_adjusted
   fixed at unavailable/not_enabled)

Model status: `baseline-constant-v0` and `quant-momentum-v0` are
pipeline vehicles for engineering acceptance — deterministic, frozen,
fully disclosed — NOT the formal campaign models. Selecting the real
baseline/quant requires Trial registration and a human-approved
release (campaign-policy §5–6); they plug in through the same
registry without touching the sealing path.
"""

import json
import uuid
from dataclasses import asdict
from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from quant.models import (
    BASELINE_MODEL_VERSION,
    MOMENTUM_MIN_BARS,
    MOMENTUM_WINDOW,
    QUANT_MODEL_VERSION,
    predict_baseline as model_predict_baseline,
    predict_quant as model_predict_quant,
)
from youwei_core.data.snapshots import freeze_daily_bars, read_snapshot
from youwei_core.db.meta import (
    campaigns,
    forecast_batches,
    forecast_cases,
    research_releases,
)
from youwei_core.jobs.worker import ClaimedJob
from youwei_core.ledger.sealing import SealRequest, SourcePrediction, seal_commit

PIPELINE_VERSION = "pipeline-v1"

# evidence window: calendar days of daily bars before the cutoff
EVIDENCE_LOOKBACK_CALENDAR_DAYS = 90


class PredictError(Exception):
    pass


class BatchPredictPayload(BaseModel):
    batch_id: uuid.UUID
    release_id: str


# --- model result adapters ----------------------------------------------------
# Preserve the existing SourcePrediction interface and version constant imports.
# All model calculation lives in the dependency-free quant package.


def predict_baseline(bars: list[dict]) -> SourcePrediction:
    """Adapt the constant vehicle's result for Ledger sealing."""
    return SourcePrediction(**asdict(model_predict_baseline(bars)))


def predict_quant(bars: list[dict] | None = None) -> SourcePrediction:
    """Adapt the frozen momentum vehicle's result for Ledger sealing."""
    return SourcePrediction(**asdict(model_predict_quant(bars)))


# registry: enabled source -> model implementation. Formal models
# register here after trial + release approval; the sealing path is
# model-agnostic.
MODELS = {
    "baseline": predict_baseline,
    "quant_model": predict_quant,
}


# --- batch prediction ---------------------------------------------------------


def make_batch_predict_handler(engine: AsyncEngine):
    """Build the `research.batch_predict` job handler."""

    async def handle_batch_predict(claimed: ClaimedJob) -> dict:
        payload = BatchPredictPayload.model_validate(claimed.payload)
        return await run_batch_predictions(
            engine, claimed, payload.batch_id, payload.release_id
        )

    return handle_batch_predict


async def run_batch_predictions(
    engine: AsyncEngine,
    claimed: ClaimedJob,
    batch_id: uuid.UUID,
    release_id: str,
) -> dict:
    """Produce and seal every case of a batch from one frozen evidence
    snapshot. Per-case failures (e.g. a case whose deadline already
    passed) are reported in the summary, never silently dropped; the
    job itself only fails on infrastructure errors."""
    async with engine.begin() as conn:
        batch = (
            await conn.execute(
                select(forecast_batches).where(forecast_batches.c.id == batch_id)
            )
        ).mappings().one_or_none()
        if batch is None:
            raise PredictError(f"batch {batch_id} not found")
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == batch.campaign_id)
            )
        ).mappings().one()
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.id == campaign.release_row_id
                )
            )
        ).mappings().one()
        cases = (
            (
                await conn.execute(
                    select(forecast_cases)
                    .where(forecast_cases.c.batch_id == batch_id)
                    .order_by(forecast_cases.c.security_id, forecast_cases.c.horizon_td)
                )
            )
            .mappings()
            .all()
        )

    if campaign.status != "active":
        raise PredictError(f"campaign {campaign.id} is not active")
    if release.release_id != release_id:
        raise PredictError(
            f"release {release_id!r} is not the campaign's release "
            f"({release.release_id!r})"
        )
    if campaign.tenant_id != claimed.tenant_id:
        raise PredictError("job tenant does not own the campaign")

    # one evidence snapshot for the whole batch, frozen at the batch's
    # decision cutoff (runs after the cutoff by construction)
    cutoff = batch.decision_cutoff_utc
    start_date = (cutoff - timedelta(days=EVIDENCE_LOOKBACK_CALENDAR_DAYS)).date()
    end_date = cutoff.date()
    security_ids = sorted(
        {str(c.security_id) for c in cases} | {str(campaign.benchmark_security_id)}
    )
    snap = await freeze_daily_bars(
        engine,
        [uuid.UUID(s) for s in security_ids],
        start_date,
        end_date,
        as_of=cutoff,
        mode="forward",
    )
    frozen = await read_snapshot(engine, snap.snapshot_id)
    bars_by_security: dict[str, list[dict]] = {}
    for bar in json.loads(frozen["content"]):
        bars_by_security.setdefault(bar["security_id"], []).append(bar)
    for bars in bars_by_security.values():
        bars.sort(key=lambda b: b["trade_date"])

    sealed, already, failures = 0, 0, []
    for case in cases:
        bars = bars_by_security.get(str(case.security_id), [])
        sources = [
            MODELS["baseline"](bars),
            MODELS["quant_model"](bars=bars),
            SourcePrediction(
                source="llm_adjusted",
                source_status="unavailable",
                reason="not_enabled",
            ),
        ]
        request = SealRequest(
            case_id=case.id,
            release_id=release_id,
            sources=sources,
            input_manifest={
                "code_version": PIPELINE_VERSION,
                "evidence_snapshot_id": str(snap.snapshot_id),
                "evidence_query": {
                    "security_ids": security_ids,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "as_of": cutoff.isoformat(),
                    "mode": "forward",
                },
            },
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
        )
        try:
            result = await seal_commit(engine, request)
        except Exception as exc:  # noqa: BLE001 — per-case, reported
            failures.append({"case_id": str(case.id), "error": str(exc)[:300]})
            continue
        if result.created:
            sealed += 1
        else:
            already += 1

    return {
        "batch_id": str(batch_id),
        "evidence_snapshot_id": str(snap.snapshot_id),
        "sealed": sealed,
        "already_sealed": already,
        "failed": failures,
    }
