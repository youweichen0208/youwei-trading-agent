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
from youwei_core.budget.service import reserve, settle
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
from youwei_core.ledger.controller import apply_phase1b_fallback
from youwei_core.ledger.evidence import build_frozen_evidence
from youwei_core.ledger.sealing import SealRequest, SourcePrediction, seal_commit
from youwei_core.llm.pricing import COST_CONFIRMED, DEFAULT_COST_MAP, CostMap, price_usage

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

    ``fetch_proposal`` is an async callable ``(case, bars) ->
    ResearchProposal | None``. ``None`` means no proposal was produced
    (e.g. the agent runtime was unavailable), which maps to unavailable
    with a reason. Otherwise the proposal is mapped through the Phase 1B
    fallback decision (controller.apply_phase1b_fallback) against the
    quant position.
    """

    async def provider(case, bars, quant, evidence_snapshot_id) -> SourcePrediction:
        try:
            proposal = await fetch_proposal(case, bars)
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
    # Conservative per-turn reservation (micro-USD); 0 disables turn-level
    # budget accounting for the research turn.
    turn_reserve_micros: int = 0


@dataclass(frozen=True)
class TurnBudgetWiring:
    """Budget accounting for one research turn (S07k).

    ``turn_reserve_micros`` is the conservative per-turn reservation made
    BEFORE the subprocess is spawned (the Controller cannot see Hermes's
    internal calls, so this is a configured upper bound, not a per-call
    estimate). ``cost_map`` prices the turn's usage for settlement. The
    reservation is released by settle on success; on failure/cancel with
    unknown cost it stays open for reconciliation.
    """

    engine: AsyncEngine
    attempt_id: uuid.UUID
    turn_reserve_micros: int
    cost_map: CostMap = DEFAULT_COST_MAP


def make_phase1b_llm_fetcher(
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    snapshot: dict,
    batch_manifest: dict,
    capability_token: str,
    agent_runtime: AgentRuntimeConfig,
    usage_sink: Callable[[dict], None] | None = None,
    budget: TurnBudgetWiring | None = None,
):
    """Build a fetch_proposal for the batch's frozen evidence.

    Each case shares the batch's single frozen snapshot (S06a); only the
    case plan differs. The Controller assembles the FrozenEvidence bundle
    (its own snapshot + case plan + batch manifest) and sends it across the
    subprocess boundary with the per-job capability token (signed by the
    worker loop when it claimed the job).

    ``usage_sink``, when provided, receives the decoded usage report dict
    after each turn so the budget layer can settle actual cost (S07k). It is
    optional so the pure codec/fetch tests and the sealing path can run
    without a budget ledger; without it the usage is dropped.

    ``budget``, when provided, wires reserve/settle around the turn:
    reserve the configured upper bound before spawning the subprocess,
    then settle the priced usage on a complete report. A placeholder rate
    settles to estimated_micros (never confirmed); an incomplete/unknown
    usage report leaves the reservation open for reconciliation (unknown
    cost is never booked as zero).
    """

    async def fetch_proposal(case, bars):
        evidence = build_frozen_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            case=case,
            snapshot=snapshot,
            batch_manifest=batch_manifest,
        )
        invocation = ResearchInvocation(
            capability_token=capability_token,
            evidence=evidence,
            config=agent_runtime.research_config,
        )
        call_key = f"{budget.attempt_id}:{case['id']}" if budget is not None else None
        if budget is not None:
            await reserve(
                budget.engine,
                run_id,
                attempt_id=budget.attempt_id,
                call_key=call_key,
                amount_micros=budget.turn_reserve_micros,
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
        if budget is not None:
            model = agent_runtime.research_config.get("model", "")
            await _settle_turn_budget(budget, run_id, call_key, model, result.usage)
        return proposal

    return fetch_proposal


async def _settle_turn_budget(
    budget: TurnBudgetWiring,
    run_id: uuid.UUID,
    call_key: str,
    model: str,
    usage: dict,
) -> None:
    """Settle a turn's reservation from its usage report.

    Only a complete turn-level report (source=session_delta, complete=True)
    is priced and settled; placeholder rates settle as estimated_micros,
    reconciled rates as settled_micros. An incomplete/unknown report leaves
    the reservation open (pending reconciliation) — unknown cost is never
    booked as zero.
    """
    if not isinstance(usage, dict):
        return  # no report: reservation stays open
    complete = usage.get("complete") is True and usage.get("source") == "session_delta"
    if not complete:
        return  # incomplete/unknown: reservation stays open for reconciliation
    cost = price_usage(budget.cost_map, model, usage)
    await settle(
        budget.engine,
        run_id,
        attempt_id=budget.attempt_id,
        call_key=call_key,
        actual_micros=cost.amount_micros,
        confirmed=(cost.status == COST_CONFIRMED),
    )


# --- batch prediction ---------------------------------------------------------


def make_batch_predict_handler(engine: AsyncEngine, *, agent_runtime: AgentRuntimeConfig | None = None):
    """Build the `research.batch_predict` job handler."""

    async def handle_batch_predict(claimed: ClaimedJob) -> dict:
        payload = BatchPredictPayload.model_validate(claimed.payload)
        return await run_batch_predictions(
            engine, claimed, payload.batch_id, payload.release_id,
            agent_runtime=agent_runtime,
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
) -> dict:
    """Produce and seal every case of a batch from one frozen evidence
    snapshot. Per-case failures (e.g. a case whose deadline already
    passed) are reported in the summary, never silently dropped; the
    job itself only fails on infrastructure errors.

    ``llm_adjusted_provider`` is an async callable
    ``(case, bars, quant, evidence_snapshot_id) -> SourcePrediction``.
    When None (Phase 1A), the llm_adjusted position is fixed at
    unavailable/not_enabled. When the campaign is Phase 1B and
    ``agent_runtime`` is provided, the provider is built from the
    agent-runtime subprocess fetcher (S07h); otherwise a Phase 1B
    campaign with no agent-runtime wiring still seals llm_adjusted as
    unavailable (agent_runtime_unavailable) — never a fake LLM value."""
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

    # Resolve the llm_adjusted provider for this batch. Phase 1A keeps the
    # position fixed unavailable/not_enabled. A Phase 1B campaign (llm_adjusted
    # enabled) with agent-runtime wiring fetches a proposal across the
    # subprocess boundary; without wiring it still seals unavailable — never a
    # fabricated LLM value.
    provider = llm_adjusted_provider
    if provider is None and "llm_adjusted" in campaign.enabled_sources:
        if agent_runtime is not None and claimed.capability_token is not None:
            budget = None
            if agent_runtime.turn_reserve_micros > 0:
                budget = TurnBudgetWiring(
                    engine=engine,
                    attempt_id=claimed.attempt_id,
                    turn_reserve_micros=agent_runtime.turn_reserve_micros,
                )
            fetch = make_phase1b_llm_fetcher(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                snapshot=frozen,
                batch_manifest=batch.batch_manifest,
                capability_token=claimed.capability_token,
                agent_runtime=agent_runtime,
                budget=budget,
            )
            provider = make_phase1b_llm_adjusted_provider(fetch)

    sealed, already, failures = 0, 0, []
    for case in cases:
        bars = bars_by_security.get(str(case.security_id), [])
        quant = MODELS["quant_model"](bars=bars)
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
            MODELS["baseline"](bars),
            quant,
            llm_adjusted,
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
