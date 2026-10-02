"""The exploration loop's Controller orchestration (S08c-3).

When a research turn returns an ``ExperimentRequest`` instead of a proposal,
this module runs the full round trip from docs/research/
s08-exploration-loop-design.md §2:

    experiment_request
      -> Core pre-dispatch registration (experiment_records; fenced to the
         current running attempt, capped per case)
      -> Runner authorization registration (control plane; the Runner
         enforces the limits and injects the snapshot)
      -> experiment instance dispatch (Runner-controlled container whose
         tools are exactly sandbox_submit/status/artifact)
      -> receipts fetch (trusted execution evidence)
      -> acceptance (verify receipts against the result, fencing re-checked,
         append-only)
      -> research re-entry (the accepted outcome rides the next turn's
         request as ExperimentContext; kind="code" citations resolve
         against it)

Cancellation and fencing: any failure or cancellation after the Runner
registration terminates the experiment there (reject new computations,
cancel running ones) before propagating; acceptance re-checks the attempt
fence at the write boundary; a newer attempt can never accept the old
attempt's experiment. Token expiry is the Runner-side backstop — the
primary control is this terminate call.

The loop is bounded by the per-case cap (MAX_EXPERIMENTS_PER_CASE in
experiment_records — registration raises ExperimentCapReachedError), so a
research instance that keeps asking for experiments surfaces as an
unavailable llm_adjusted, never an infinite loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentContext,
    ExperimentInvocationRequest,
    ExperimentLimits,
    ExperimentRequest,
    ExperimentRuntimeConfig,
    experiment_context_from_result,
)
from youwei_contracts.sandbox import SnapshotBundle

from youwei_core.db.meta import attempts
from youwei_core.ledger.agent_client import AgentRuntimeError
from youwei_core.ledger.evidence import build_frozen_evidence
from youwei_core.ledger.experiment_client import (
    ExperimentBinding,
    ExperimentRunnerClient,
    ExperimentRunnerError,
    experiment_binding_from_auth,
    run_experiment_instance,
)
from youwei_core.ledger.experiment_records import (
    ExperimentRegistration,
    accept_experiment,
    register_experiment,
)
from youwei_core.ledger.research_client import (
    ResearchRunnerClient,
    ResearchSigningKey,
    build_research_request,
    evidence_sha256,
    run_research_via_runner,
)

# How far the tool grant may outlive the dispatch grant: the experiment's
# own duration budget plus a margin. The tool grant is the Runner-side
# backstop (the terminate call is the primary control), so it must cover
# the whole experiment but no more than necessary.
_TOOL_GRANT_MARGIN_SECONDS = 300.0


class ExperimentOrchestrationError(Exception):
    """The exploration loop failed; the llm_adjusted position maps to
    unavailable (the provider layer catches and records the reason)."""


async def active_lease_expiry(
    engine: AsyncEngine, attempt_id: uuid.UUID, attempt_no: int
) -> datetime:
    """expiry_provider that re-checks the attempt's LIVE lease (the worker
    heartbeat extends it while the handler runs; the claim-time snapshot
    goes stale on long multi-turn cases). A missing/terminal attempt raises
    so the Runner-side authorization fails closed."""
    async with engine.connect() as conn:
        lease = (
            await conn.execute(
                select(attempts.c.lease_expires_at).where(
                    attempts.c.id == attempt_id,
                    attempts.c.attempt_no == attempt_no,
                    attempts.c.status == "running",
                )
            )
        ).scalar_one_or_none()
    if lease is None:
        raise ExperimentOrchestrationError(
            f"attempt {attempt_no} is no longer running; refusing to sign grants"
        )
    return lease.astimezone(UTC) if lease.tzinfo else lease


@dataclass(frozen=True)
class ExperimentWiring:
    """Everything the exploration loop needs beyond the research link."""

    engine: AsyncEngine
    client: ExperimentRunnerClient  # control plane + dispatch (same Runner)
    key: ResearchSigningKey
    limits: ExperimentLimits


def make_experiment_fetcher(
    *,
    wiring: ExperimentWiring,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_no: int,
    snapshot: dict,
    batch_manifest: dict,
    research_client: ResearchRunnerClient,
    research_config: dict,
):
    """Build a ``fetch_proposal(case, bars, quant)`` that runs the exploration loop.

    Each case starts with a plain research turn; an ExperimentRequest answer
    triggers the registered->dispatched->verified->accepted experiment, and
    the accepted outcome rides the re-entry turn(s). The loop ends with a
    proposal, an error (-> unavailable), or the per-case cap.
    """
    snapshot_id = uuid.UUID(snapshot["id"])

    async def fetch_proposal(case, bars, quant):
        evidence = build_frozen_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            case=case,
            snapshot=snapshot,
            batch_manifest=batch_manifest,
            quant=quant,
        )
        ev_sha = evidence_sha256(evidence)

        async def expiry_provider() -> datetime:
            return await active_lease_expiry(wiring.engine, attempt_id, attempt_no)

        experiments: list[ExperimentContext] = []
        while True:
            request = build_research_request(
                invocation_id=uuid.uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                attempt_no=attempt_no,
                case_id=evidence.case.case_id,
                evidence=evidence,
                evidence_sha256=ev_sha,
                exec_config_version=wiring.key.exec_config_version,
                config=research_config,
            )
            # the re-entry turns carry the accepted outcomes of this case's
            # earlier experiments (build_research_request fills experiments)
            request = request.model_copy(
                update={"experiments": list(experiments)}
            )
            result = await run_research_via_runner(
                research_client,
                wiring.key,
                request,
                expiry_provider=expiry_provider,
            )
            if result.proposal is not None:
                _validate_experiment_citations(result.proposal, experiments)
                return result.proposal
            if result.experiment_request is None:
                raise AgentRuntimeError(
                    "research invocation returned neither proposal nor "
                    "experiment request"
                )
            context = await _run_experiment(
                wiring,
                request=result.experiment_request,
                case_id=evidence.case.case_id,
                evidence_sha256=ev_sha,
                run_id=run_id,
                tenant_id=tenant_id,
                job_id=job_id,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                snapshot_id=snapshot_id,
                snapshot=snapshot,
                model=str(research_config.get("model", "")),
                expiry_provider=expiry_provider,
            )
            experiments.append(context)

    return fetch_proposal


async def _run_experiment(
    wiring: ExperimentWiring,
    *,
    request: ExperimentRequest,
    case_id: uuid.UUID,
    evidence_sha256: str,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_no: int,
    snapshot_id: uuid.UUID,
    snapshot: dict,
    model: str,
    expiry_provider: Callable[[], Awaitable[datetime]],
) -> ExperimentContext:
    """One full experiment round trip: register (Core + Runner), dispatch,
    verify receipts, accept, and return the re-entry context.

    Any failure (including cancellation) after the Runner registration
    terminates the experiment there — reject new computations, cancel
    running ones — before propagating. Acceptance re-checks the attempt
    fence at the write boundary.
    """
    experiment_id = uuid.uuid4()
    registration = ExperimentRegistration(
        experiment_invocation_id=experiment_id,
        tenant_id=tenant_id,
        run_id=run_id,
        job_id=job_id,
        attempt_id=attempt_id,
        attempt_no=attempt_no,
        case_id=case_id,
        evidence_sha256=evidence_sha256,
        exec_config_version=wiring.key.exec_config_version,
        question=request.question,
        motivation=request.motivation,
        requested_shape=request.requested_shape,
        limits=wiring.limits,
        snapshot_id=snapshot_id,
    )
    await register_experiment(wiring.engine, registration)
    auth = ExperimentAuthorization(
        experiment_invocation_id=experiment_id,
        tenant_id=tenant_id,
        run_id=run_id,
        job_id=job_id,
        attempt_id=attempt_id,
        attempt_no=attempt_no,
        case_id=case_id,
        evidence_sha256=evidence_sha256,
        exec_config_version=wiring.key.exec_config_version,
        limits=wiring.limits,
        snapshot=SnapshotBundle(
            snapshot_id=snapshot_id,
            content=snapshot["content"],
            manifest=snapshot["manifest"],
        ),
    )
    binding = experiment_binding_from_auth(auth)
    await wiring.client.register_authorization(
        key=wiring.key, auth=auth, binding=binding,
        expiry_provider=expiry_provider,
    )
    try:
        exp = await expiry_provider()
        tool_exp = datetime.now(UTC) + timedelta(
            seconds=(
                wiring.limits.max_total_duration_seconds
                + _TOOL_GRANT_MARGIN_SECONDS
            )
        )
        invocation = ExperimentInvocationRequest(
            experiment_invocation_id=experiment_id,
            tenant_id=tenant_id,
            run_id=run_id,
            job_id=job_id,
            attempt_no=attempt_no,
            case_id=case_id,
            evidence_sha256=evidence_sha256,
            exec_config_version=wiring.key.exec_config_version,
            question=request,
            snapshot_manifest=snapshot["manifest"],
            config=ExperimentRuntimeConfig(
                model=model,
            ),
        )
        result = await run_experiment_instance(
            wiring.client,
            wiring.key,
            binding,
            invocation,
            expiry_provider=expiry_provider,
            tool_exp=tool_exp,
        )
        if not result.ok or result.result is None:
            raise ExperimentOrchestrationError(
                f"experiment instance failed: {result.error or 'unknown'}"
            )
        receipts = await wiring.client.receipts(
            key=wiring.key, binding=binding, expiry_provider=expiry_provider
        )
        await accept_experiment(
            wiring.engine,
            experiment_invocation_id=experiment_id,
            result=result.result,
            receipts=receipts,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            tenant_id=tenant_id,
        )
        # The experiment is accepted and recorded: close it on the Runner so
        # no further computations can attach to it (best-effort; token
        # expiry is the backstop).
        await _best_effort_terminate(wiring, binding, expiry_provider)
        return experiment_context_from_result(
            experiment_id, request.question, result.result
        )
    except BaseException:
        # Cancellation, lease loss, dispatch failure, or evidence mismatch:
        # terminate on the Runner (reject new computations, cancel running
        # ones) before propagating. Best-effort — the original error wins.
        await _best_effort_terminate(wiring, binding, expiry_provider)
        raise


def _validate_experiment_citations(proposal, experiments: list) -> None:
    """Controller-side citation check: kind="code" references must resolve
    against the accepted outcomes of THIS case's experiments (the runtime
    already checked against the carried contexts; this re-verifies against
    the same accepted set before the proposal is returned to the seal
    path)."""
    for reference in proposal.references:
        if reference.kind == "code":
            from youwei_contracts.experiment import parse_experiment_locator

            try:
                experiment_id, _, _ = parse_experiment_locator(reference.locator)
            except ValueError as exc:
                raise AgentRuntimeError(
                    f"invalid experiment citation: {exc}"
                ) from exc
            if not any(
                e.experiment_invocation_id == experiment_id for e in experiments
            ):
                raise AgentRuntimeError(
                    "experiment citation names an experiment that is not "
                    "part of this case's accepted outcomes"
                )


async def _best_effort_terminate(
    wiring: ExperimentWiring,
    binding: ExperimentBinding,
    expiry_provider: Callable[[], Awaitable[datetime]],
) -> None:
    """Terminate the experiment on the Runner; swallow transport errors —
    the token expiry backstop and the Runner's lease reaping cover the
    residual window, and the original error (if any) must win."""
    try:
        await wiring.client.terminate(
            key=wiring.key, binding=binding, expiry_provider=expiry_provider
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
