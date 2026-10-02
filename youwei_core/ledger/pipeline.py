"""Forward prediction pipeline with release-bound numeric models.

The Controller-side job that turns a planned batch into sealed
predictions:

1. freeze ONE evidence snapshot for the whole batch at the batch's
   decision cutoff (PIT forward view: only data usable at the cutoff;
   architecture §3.1 — one manifest shared by the batch's research)
2. run the registered model implementations over the frozen bars
3. seal each case's three positions atomically (Phase 1A: llm_adjusted
   fixed at unavailable/not_enabled)

Old releases retain their `quant-momentum-v0` engineering vehicle. A release
explicitly selecting `quant-logistic-ridge-v1` carries all three horizon JSON
artifacts; the registry checks them before any snapshot or prediction write.
Trial registration and human release approval remain separate requirements.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Awaitable, Callable

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
from youwei_core.ledger.agent_client import (
    AgentRuntimeError,
    ResearchInvocation,
    run_agent_research,
)
from youwei_core.ledger.research_client import (
    RunnerResearchConfig,
    make_runner_research_fetcher,
)
from youwei_core.ledger.controller import apply_phase1b_fallback
from youwei_core.ledger.evidence import build_frozen_evidence
from youwei_core.ledger.model_registry import build_release_predictor
from youwei_core.ledger.sealing import SealRequest, SourcePrediction, seal_commit

PIPELINE_VERSION = "pipeline-v1"


async def _lease_expiry(claimed: ClaimedJob):
    """expiry_provider for the Runner research link: the grant's exp must not
    outlive the attempt's lease. Claim-time lease is the conservative floor;
    the DB-level active-lease re-check is exercised in the SG DB tests (S07m-3)."""
    from datetime import UTC, datetime

    return claimed.lease_expires_at.astimezone(UTC) if claimed.lease_expires_at.tzinfo else claimed.lease_expires_at

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


# --- llm_adjusted provider (Phase 1B seam) -----------------------------------
# The llm_adjusted position is fixed at unavailable/not_enabled in Phase 1A.
# In Phase 1B it comes from a Hermes ResearchProposal. Because the
# agent-runtime is a separate Python 3.14 process, the proposal is fetched
# across a process boundary by the caller; this module only maps a proposal
# (or its absence) into the sealable SourcePrediction position.


async def _phase1a_llm_adjusted(case, bars, quant, evidence_snapshot_id) -> SourcePrediction:
    """Phase 1A: llm_adjusted is fixed unavailable with reason=not_enabled."""
    return SourcePrediction(
        source="llm_adjusted",
        source_status="unavailable",
        reason="not_enabled",
    )


def make_phase1b_llm_adjusted_provider(fetch_proposal):
    """Build an llm_adjusted provider from a proposal fetcher.

    ``fetch_proposal`` is an async callable ``(case, bars, quant) ->
    ResearchProposal | None``. ``None`` means no proposal was produced
    (e.g. the agent runtime was unavailable), which maps to unavailable
    with a reason. Otherwise the proposal is mapped through the Phase 1B
    fallback decision (controller.apply_phase1b_fallback) against the
    quant position.
    """

    async def provider(case, bars, quant, evidence_snapshot_id) -> SourcePrediction:
        try:
            proposal = await fetch_proposal(case, bars, quant)
        except Exception as exc:  # noqa: BLE001 — LLM/runtime failure -> unavailable
            return SourcePrediction(
                source="llm_adjusted",
                source_status="unavailable",
                reason=f"agent_runtime_error: {type(exc).__name__}",
                evidence_snapshot_id=evidence_snapshot_id,
            )
        if proposal is None:
            return SourcePrediction(
                source="llm_adjusted",
                source_status="unavailable",
                reason="agent_runtime_unavailable",
                evidence_snapshot_id=evidence_snapshot_id,
            )
        reception = apply_phase1b_fallback(
            proposal,
            quant_prediction=quant,
            evidence_snapshot_id=evidence_snapshot_id,
        )
        return reception.prediction

    return provider


# --- agent-runtime subprocess fetch (S07h) -----------------------------------
# The Phase 1B llm_adjusted position comes from Hermes, reached through the
# agent-runtime subprocess boundary. This factory binds the per-batch frozen
# evidence plus the Controller's capability token and gateway config into a
# ``fetch_proposal(case, bars) -> ResearchProposal`` for the provider above.


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Controller-side wiring to reach the agent-runtime subprocess."""

    research_config: dict  # runtime.ResearchConfig kwargs
    process_factory: Callable[[], Awaitable]
    timeout_seconds: float


def make_phase1b_llm_fetcher(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    snapshot: dict,
    batch_manifest: dict,
    capability_token: str,
    agent_runtime: AgentRuntimeConfig,
    usage_sink: Callable[[dict], None] | None = None,
):
    """Build a fetch_proposal for the batch's frozen evidence.

    Each case shares the batch's single frozen snapshot (S06a); only the
    case plan differs. The Controller assembles the FrozenEvidence bundle
    (its own snapshot + case plan + batch manifest + the case's quant
    prediction) and sends it across the subprocess boundary with the
    per-job capability token (signed by the worker loop when it claimed
    the job).

    ``usage_sink``, when provided, receives the decoded usage report dict
    after each turn for observability; it is optional so the pure codec/fetch
    tests and the sealing path can run without it.
    """

    async def fetch_proposal(case, bars, quant):
        evidence = build_frozen_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            case=case,
            snapshot=snapshot,
            batch_manifest=batch_manifest,
            quant=quant,
        )
        invocation = ResearchInvocation(
            capability_token=capability_token,
            evidence=evidence,
            config=agent_runtime.research_config,
        )
        result = await run_agent_research(
            invocation,
            process_factory=agent_runtime.process_factory,
            timeout_seconds=agent_runtime.timeout_seconds,
        )
        proposal = result.proposal
        # Controller-side binding check (defense in depth): the proposal must
        # answer the exact case this bundle was sent for — never trust the
        # wire's run_id/case_id blindly.
        if proposal.run_id != run_id or proposal.case_id != case["id"]:
            raise AgentRuntimeError(
                "proposal run_id/case_id does not match the frozen evidence"
            )
        if usage_sink is not None:
            usage_sink(result.usage)
        return proposal

    return fetch_proposal


# --- batch prediction ---------------------------------------------------------


def make_batch_predict_handler(
    engine: AsyncEngine,
    *,
    agent_runtime: AgentRuntimeConfig | None = None,
    runner_research: RunnerResearchConfig | None = None,
):
    """Build the `research.batch_predict` job handler."""

    async def handle_batch_predict(claimed: ClaimedJob) -> dict:
        payload = BatchPredictPayload.model_validate(claimed.payload)
        return await run_batch_predictions(
            engine, claimed, payload.batch_id, payload.release_id,
            agent_runtime=agent_runtime,
            runner_research=runner_research,
        )

    return handle_batch_predict


async def run_batch_predictions(
    engine: AsyncEngine,
    claimed: ClaimedJob,
    batch_id: uuid.UUID,
    release_id: str,
    *,
    llm_adjusted_provider=None,
    agent_runtime: AgentRuntimeConfig | None = None,
    runner_research: RunnerResearchConfig | None = None,
) -> dict:
    """Produce and seal every case of a batch from one frozen evidence
    snapshot. Per-case failures (e.g. a case whose deadline already
    passed) are reported in the summary, never silently dropped; the
    job itself only fails on infrastructure errors.

    ``llm_adjusted_provider`` is an async callable
    ``(case, bars, quant, evidence_snapshot_id) -> SourcePrediction``.
    When None (Phase 1A), the llm_adjusted position is fixed at
    unavailable/not_enabled. When the campaign is Phase 1B and either
    ``agent_runtime`` (local subprocess, S07h) or ``runner_research``
    (Runner-controlled container, S07m) is provided, the provider fetches a
    proposal across the corresponding boundary — carrying the case's quant
    prediction inside the frozen evidence; without wiring it still seals
    llm_adjusted as unavailable (agent_runtime_unavailable) — never a fake
    LLM value."""
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
    predictor = await build_release_predictor(
        engine,
        release_manifest=release.manifest,
        batch_manifest=batch.batch_manifest,
        cutoff=cutoff,
        benchmark_security_id=str(campaign.benchmark_security_id),
    )
    start_date = (cutoff - timedelta(days=predictor.lookback_calendar_days)).date()
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

    # Resolve the llm_adjusted provider for this batch. Phase 1A keeps the
    # position fixed unavailable/not_enabled. A Phase 1B campaign (llm_adjusted
    # enabled) with agent-runtime wiring fetches a proposal across the
    # subprocess boundary; without wiring it still seals unavailable — never a
    # fabricated LLM value.
    provider = llm_adjusted_provider
    if provider is None and "llm_adjusted" in campaign.enabled_sources:
        if runner_research is not None:
            # S07m: research runs in the Runner-controlled container, one
            # invocation per case, authorized by the Controller's Ed25519 key.
            # S08c-3: with experiment wiring attached, the fetcher runs the
            # exploration loop (experiment request -> registered -> sandbox
            # -> verified -> accepted -> re-entry) instead of a single turn.
            if (
                runner_research.experiment_client is not None
                and runner_research.experiment_limits is not None
            ):
                from youwei_core.ledger.experiment_orchestrator import (
                    ExperimentWiring,
                    make_experiment_fetcher,
                )

                wiring = ExperimentWiring(
                    engine=engine,
                    client=runner_research.experiment_client,
                    key=runner_research.key,
                    limits=runner_research.experiment_limits,
                )
                fetch = make_experiment_fetcher(
                    wiring=wiring,
                    run_id=claimed.run_id,
                    tenant_id=claimed.tenant_id,
                    job_id=claimed.job_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    snapshot=frozen,
                    batch_manifest=batch.batch_manifest,
                    research_client=runner_research.client,
                    research_config=runner_research.research_config,
                )
            else:
                fetch = make_runner_research_fetcher(
                    run_id=claimed.run_id,
                    tenant_id=claimed.tenant_id,
                    job_id=claimed.job_id,
                    attempt_no=claimed.attempt_no,
                    snapshot=frozen,
                    batch_manifest=batch.batch_manifest,
                    client=runner_research.client,
                    key=runner_research.key,
                    expiry_provider=lambda: _lease_expiry(claimed),
                    research_config=runner_research.research_config,
                )
            provider = make_phase1b_llm_adjusted_provider(fetch)
        elif agent_runtime is not None and claimed.capability_token is not None:
            fetch = make_phase1b_llm_fetcher(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                snapshot=frozen,
                batch_manifest=batch.batch_manifest,
                capability_token=claimed.capability_token,
                agent_runtime=agent_runtime,
            )
            provider = make_phase1b_llm_adjusted_provider(fetch)

    sealed, already, failures = 0, 0, []
    for case in cases:
        bars = bars_by_security.get(str(case.security_id), [])
        quant = predictor.predict_case(case, bars_by_security)
        # the quant position consumed the batch's frozen evidence: the
        # per-prediction reference makes the input traceable. Attached
        # for unavailable results too — the snapshot records the
        # absence that made the model unable to run. The constant
        # baseline consumes no evidence and stays null (honest, not
        # decorative).
        if quant.evidence_snapshot_id is None:
            quant = quant.model_copy(
                update={"evidence_snapshot_id": snap.snapshot_id}
            )
        llm_adjusted = await (
            _phase1a_llm_adjusted(case, bars, quant, snap.snapshot_id)
            if provider is None
            else provider(case, bars, quant, snap.snapshot_id)
        )
        sources = [
            predict_baseline(bars),
            quant,
            llm_adjusted,
        ]
        request = SealRequest(
            case_id=case.id,
            release_id=release_id,
            sources=sources,
            input_manifest={
                "code_version": (
                    PIPELINE_VERSION if predictor.model_version == QUANT_MODEL_VERSION
                    else "pipeline-logistic-v1"
                ),
                **predictor.provenance(case.horizon_td),
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
