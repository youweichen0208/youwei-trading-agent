"""Core-side experiment registration and acceptance (S08c, D2 minimal
requirements 1, 4, 5).

The exploration loop (docs/research/s08-exploration-loop-design.md) runs:

    research turn -> ExperimentRequest
      -> Controller registers the experiment BEFORE dispatch (this module,
         ``register_experiment``: bindings + limits + the frozen snapshot,
         fenced to the current running attempt, capped per case)
      -> Runner executes (registered authorization, persistent receipts)
      -> Controller re-verifies the Runner's receipts against the
         experiment result and accepts append-only (``accept_experiment``:
         fencing re-checked at the write boundary)
      -> research re-entry may cite ``experiment:<id>/computations/<cid>/
         artifacts/<path>`` (``resolve_experiment_reference`` / the
         DB-backed ``resolve_experiment_artifact``)

Trust model: the experiment instance's ``ExperimentResult`` is a CLAIM; the
Runner's ``ExperimentComputationReceipt`` is the evidence. Acceptance
verifies every claimed computation against a receipt (code hash, terminal
status, artifact manifests, snapshot hash, single image) and records both
append-only. Nothing from the instance enters the ledger unverified.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentComputationReceipt,
    ExperimentLimits,
    ExperimentResult,
    parse_experiment_locator,
)
from youwei_core.db.meta import (
    attempts,
    campaigns,
    experiment_records,
    experiment_outcomes,
    forecast_batches,
    forecast_cases,
    jobs,
    snapshots,
)

# Per-case cap on registered experiments (design §2: "每 case 实验次数有上限
# (K, 默认 1-2)"). Registrations from fenced/stale attempts still count:
# they are facts about work that was authorized.
MAX_EXPERIMENTS_PER_CASE = 2


class ExperimentRecordError(Exception):
    """Base class for experiment registration/acceptance failures."""


class ExperimentFencedError(ExperimentRecordError):
    """The attempt is not the job's current running attempt (or the caller
    is not the attempt that registered the experiment)."""


class ExperimentConflictError(ExperimentRecordError):
    """The same id was already registered/accepted with different content."""


class ExperimentCapReachedError(ExperimentRecordError):
    """The case already has the maximum number of registered experiments."""


class ExperimentEvidenceError(ExperimentRecordError):
    """The Runner's receipts do not substantiate the experiment result."""


class ExperimentReferenceError(ExperimentRecordError):
    """An experiment artifact reference cannot be resolved to a verified
    artifact of an accepted experiment."""


@dataclass(frozen=True)
class ExperimentRegistration:
    """The pre-dispatch registration payload (D2 requirement 1): everything
    the Controller binds before the experiment instance is spawned.

    ``evidence_sha256`` is the parent research turn's FrozenEvidence hash —
    it ties the experiment to the research evidence it was asked about.
    ``snapshot_id`` references the immutable frozen snapshot the Runner
    injects into every computation (the instance never chooses its input).
    """

    experiment_invocation_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int
    case_id: uuid.UUID
    evidence_sha256: str
    exec_config_version: str
    question: str
    motivation: str
    requested_shape: str
    limits: ExperimentLimits
    snapshot_id: uuid.UUID


@dataclass(frozen=True)
class RegistrationResult:
    created: bool


@dataclass(frozen=True)
class Acceptance:
    created: bool


@dataclass(frozen=True)
class ExperimentOutcome:
    """An accepted experiment: the instance's result plus the Runner's
    receipts (trusted execution evidence), as recorded append-only."""

    experiment_invocation_id: uuid.UUID
    result: ExperimentResult
    receipts: tuple[ExperimentComputationReceipt, ...]
    image: str
    snapshot_sha256: str
    accepted_attempt_id: uuid.UUID
    accepted_attempt_no: int


@dataclass(frozen=True)
class ResolvedExperimentArtifact:
    """A reference resolved against accepted receipts: the artifact's
    verified manifest plus the computation receipt that produced it."""

    manifest: ArtifactManifest
    receipt: ExperimentComputationReceipt


# --- pure verification --------------------------------------------------------


def verify_experiment_evidence(
    *,
    experiment_invocation_id: uuid.UUID,
    result: ExperimentResult,
    receipts: Sequence[ExperimentComputationReceipt],
    snapshot_content_sha256: str,
) -> str:
    """Verify the Runner's receipts substantiate the experiment result.

    Rules (D2 requirement 4 — trusted execution evidence):
    - every receipt belongs to this experiment and is terminal (no
      computation may still be running at acceptance);
    - every receipt recorded the registered snapshot (the Runner injects it;
      a different hash means the computation ran on other input);
    - all receipts share one sandbox image (one experiment, one deployment);
    - every computation the result claims has a receipt with the SAME code
      hash, terminal status, and artifact manifests. Receipts the instance
      did not report are kept as evidence (the instance may omit failures).

    Returns the (single) image the computations ran on. Raises
    ExperimentEvidenceError on the first violation.
    """
    by_computation: dict[uuid.UUID, ExperimentComputationReceipt] = {}
    image: str | None = None
    for receipt in receipts:
        if receipt.experiment_invocation_id != experiment_invocation_id:
            raise ExperimentEvidenceError(
                "receipt belongs to another experiment "
                f"({receipt.experiment_invocation_id})"
            )
        if receipt.computation_id in by_computation:
            raise ExperimentEvidenceError(
                f"duplicate receipt for computation {receipt.computation_id}"
            )
        if receipt.status == "running":
            raise ExperimentEvidenceError(
                f"computation {receipt.computation_id} is still running; "
                "an experiment cannot be accepted while computations run"
            )
        if receipt.snapshot_sha256 != snapshot_content_sha256:
            raise ExperimentEvidenceError(
                f"computation {receipt.computation_id} ran on a different "
                "snapshot than the registered one"
            )
        if image is None:
            image = receipt.image
        elif receipt.image != image:
            raise ExperimentEvidenceError(
                "receipts report inconsistent sandbox images "
                f"({receipt.image} vs {image})"
            )
        by_computation[receipt.computation_id] = receipt

    for computation in result.computations:
        receipt = by_computation.get(computation.computation_id)
        if receipt is None:
            raise ExperimentEvidenceError(
                f"result claims computation {computation.computation_id} "
                "with no runner receipt"
            )
        if receipt.code_sha256 != computation.code_sha256:
            raise ExperimentEvidenceError(
                f"computation {computation.computation_id} code hash does "
                "not match its receipt"
            )
        if receipt.status != computation.status:
            raise ExperimentEvidenceError(
                f"computation {computation.computation_id} status "
                f"{computation.status!r} does not match the receipt's "
                f"{receipt.status!r}"
            )
        claimed = {a.path: a for a in computation.artifacts}
        recorded = {a.path: a for a in receipt.artifacts}
        if claimed != recorded:
            raise ExperimentEvidenceError(
                f"computation {computation.computation_id} artifact "
                "manifests do not match its receipt"
            )
    if image is None:
        # A findings-only experiment (no computations): there is nothing to
        # attribute an image to. The empty string keeps the outcome row's
        # NOT NULL honest — no computation ran.
        return ""
    return image


def resolve_experiment_reference(
    *,
    outcome_receipts: Sequence[ExperimentComputationReceipt],
    outcome_experiment_id: uuid.UUID,
    locator: str,
) -> ResolvedExperimentArtifact:
    """Resolve ``experiment:<id>/computations/<cid>/artifacts/<path>``
    against an ACCEPTED experiment's receipts (D2 requirement 5: the
    computation id is part of the locator, so the same filename from two
    computations can never collide)."""
    try:
        experiment_id, computation_id, path = parse_experiment_locator(locator)
    except ValueError as exc:
        raise ExperimentReferenceError(f"invalid experiment locator: {exc}") from exc
    if experiment_id != outcome_experiment_id:
        raise ExperimentReferenceError(
            f"locator names another experiment ({experiment_id})"
        )
    receipt = next(
        (r for r in outcome_receipts if r.computation_id == computation_id), None
    )
    if receipt is None:
        raise ExperimentReferenceError(
            f"computation {computation_id} is not part of the accepted experiment"
        )
    manifest = next((a for a in receipt.artifacts if a.path == path), None)
    if manifest is None:
        raise ExperimentReferenceError(
            f"artifact {path!r} is not in computation {computation_id}'s receipt"
        )
    return ResolvedExperimentArtifact(manifest=manifest, receipt=receipt)


# --- registration (pre-dispatch, D2 requirement 1) -----------------------------


async def register_experiment(
    engine: AsyncEngine, registration: ExperimentRegistration
) -> RegistrationResult:
    """Persist the experiment authorization BEFORE any dispatch.

    One short transaction: idempotency first (same id + same content is a
    no-op), then the fencing check (the attempt must be the job's current
    running attempt with a live lease), the case binding (the case must
    belong to the caller's tenant), the snapshot existence, and the per-case
    cap. Any failure leaves no row behind.
    """
    if registration.attempt_no < 1:
        raise ExperimentRecordError("attempt_no must be positive")
    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(experiment_records).where(
                    experiment_records.c.id == registration.experiment_invocation_id
                )
            )
        ).mappings().one_or_none()
        if existing is not None:
            _assert_registration_matches(existing, registration)
            return RegistrationResult(created=False)

        db_now = (
            await conn.execute(select(func.clock_timestamp()))
        ).scalar_one()
        await _check_fenced(
            conn,
            tenant_id=registration.tenant_id,
            run_id=registration.run_id,
            job_id=registration.job_id,
            attempt_id=registration.attempt_id,
            attempt_no=registration.attempt_no,
            db_now=db_now,
        )
        case = (
            await conn.execute(
                select(
                    forecast_cases.c.id,
                    forecast_cases.c.prediction_deadline_utc,
                    campaigns.c.tenant_id.label("campaign_tenant_id"),
                )
                .select_from(forecast_cases)
                .join(forecast_batches, forecast_batches.c.id == forecast_cases.c.batch_id)
                .join(campaigns, campaigns.c.id == forecast_batches.c.campaign_id)
                .where(forecast_cases.c.id == registration.case_id)
            )
        ).mappings().one_or_none()
        if case is None:
            raise ExperimentRecordError(f"case {registration.case_id} not found")
        if case.campaign_tenant_id != registration.tenant_id:
            raise ExperimentFencedError(
                "case belongs to a different tenant than the registering attempt"
            )
        if db_now > case.prediction_deadline_utc:
            raise ExperimentRecordError(
                "case prediction deadline has passed; an experiment dispatched "
                "now cannot produce an on-time prediction"
            )
        snapshot_row = (
            await conn.execute(
                select(snapshots.c.id, snapshots.c.content_sha256).where(
                    snapshots.c.id == registration.snapshot_id
                )
            )
        ).one_or_none()
        if snapshot_row is None:
            raise ExperimentRecordError(
                f"snapshot {registration.snapshot_id} not found"
            )
        prior = (
            await conn.execute(
                select(func.count()).select_from(experiment_records).where(
                    experiment_records.c.case_id == registration.case_id
                )
            )
        ).scalar_one()
        if prior >= MAX_EXPERIMENTS_PER_CASE:
            raise ExperimentCapReachedError(
                f"case {registration.case_id} already has {prior} registered "
                f"experiments (cap {MAX_EXPERIMENTS_PER_CASE})"
            )
        await conn.execute(
            experiment_records.insert().values(
                id=registration.experiment_invocation_id,
                tenant_id=registration.tenant_id,
                run_id=registration.run_id,
                job_id=registration.job_id,
                attempt_id=registration.attempt_id,
                attempt_no=registration.attempt_no,
                case_id=registration.case_id,
                evidence_sha256=registration.evidence_sha256,
                exec_config_version=registration.exec_config_version,
                question=registration.question,
                motivation=registration.motivation,
                requested_shape=registration.requested_shape,
                limits=registration.limits.model_dump(mode="json"),
                snapshot_id=registration.snapshot_id,
            )
        )
        return RegistrationResult(created=True)


def _assert_registration_matches(row, registration: ExperimentRegistration) -> None:
    same = (
        row.tenant_id == registration.tenant_id
        and row.run_id == registration.run_id
        and row.job_id == registration.job_id
        and row.attempt_id == registration.attempt_id
        and row.attempt_no == registration.attempt_no
        and row.case_id == registration.case_id
        and row.evidence_sha256 == registration.evidence_sha256
        and row.exec_config_version == registration.exec_config_version
        and row.question == registration.question
        and row.motivation == registration.motivation
        and row.requested_shape == registration.requested_shape
        and row.limits == registration.limits.model_dump(mode="json")
        and row.snapshot_id == registration.snapshot_id
    )
    if not same:
        raise ExperimentConflictError(
            "experiment already registered with different content"
        )


async def _check_fenced(
    conn,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_no: int,
    db_now: datetime,
) -> None:
    """The attempt must be the job's current running attempt with a live
    lease, and the job must belong to the caller's run/tenant (mirrors the
    sealing fence; attempt_no is the fencing token)."""
    row = (
        await conn.execute(
            select(
                attempts.c.status.label("attempt_status"),
                attempts.c.lease_expires_at,
                jobs.c.id.label("job_id"),
                jobs.c.attempt_count,
                jobs.c.status.label("job_status"),
                jobs.c.tenant_id,
                jobs.c.run_id,
            )
            .select_from(attempts)
            .join(jobs, jobs.c.id == attempts.c.job_id)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.attempt_no == attempt_no,
            )
        )
    ).mappings().one_or_none()
    if (
        row is None
        or row.job_id != job_id
        or row.attempt_count != attempt_no
        or row.job_status != "running"
        or row.attempt_status != "running"
        or row.lease_expires_at <= db_now
    ):
        raise ExperimentFencedError(
            f"attempt {attempt_no} is not the job's current running attempt; "
            "experiment not registered"
        )
    if row.tenant_id != tenant_id or row.run_id != run_id:
        raise ExperimentFencedError(
            "attempt belongs to a different tenant/run than the registration"
        )


# --- acceptance (D2 requirement 4: verify evidence, then record) ----------------


async def accept_experiment(
    engine: AsyncEngine,
    *,
    experiment_invocation_id: uuid.UUID,
    result: ExperimentResult,
    receipts: Sequence[ExperimentComputationReceipt],
    attempt_id: uuid.UUID,
    attempt_no: int,
    tenant_id: uuid.UUID,
) -> Acceptance:
    """Verify the receipts against the result and record the outcome.

    Fencing is re-checked at the write boundary: the accepting attempt must
    be the job's current running attempt AND the attempt that registered the
    experiment (a newer attempt means the experiment was terminated). The
    verification itself is pure (``verify_experiment_evidence``); the
    registered snapshot's content hash is the expected receipt hash.
    """
    async with engine.begin() as conn:
        record = (
            await conn.execute(
                select(experiment_records).where(
                    experiment_records.c.id == experiment_invocation_id
                )
            )
        ).mappings().one_or_none()
        if record is None:
            raise ExperimentRecordError(
                f"experiment {experiment_invocation_id} is not registered"
            )
        snapshot = (
            await conn.execute(
                select(snapshots.c.content_sha256).where(
                    snapshots.c.id == record.snapshot_id
                )
            )
        ).scalar_one()
        image = verify_experiment_evidence(
            experiment_invocation_id=experiment_invocation_id,
            result=result,
            receipts=receipts,
            snapshot_content_sha256=snapshot,
        )
        db_now = (
            await conn.execute(select(func.clock_timestamp()))
        ).scalar_one()
        await _check_fenced(
            conn,
            tenant_id=tenant_id,
            run_id=record.run_id,
            job_id=record.job_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            db_now=db_now,
        )
        if record.tenant_id != tenant_id:
            raise ExperimentFencedError(
                "experiment was registered by another tenant"
            )
        if record.attempt_id != attempt_id or record.attempt_no != attempt_no:
            raise ExperimentFencedError(
                "experiment was registered by another attempt; its result "
                "cannot be accepted by this attempt"
            )
        existing = (
            await conn.execute(
                select(experiment_outcomes).where(
                    experiment_outcomes.c.experiment_invocation_id
                    == experiment_invocation_id
                )
            )
        ).mappings().one_or_none()
        result_json = result.model_dump(mode="json")
        receipts_json = [r.model_dump(mode="json") for r in receipts]
        if existing is not None:
            if (
                existing.result != result_json
                or existing.receipts != receipts_json
            ):
                raise ExperimentConflictError(
                    "experiment already accepted with different content"
                )
            return Acceptance(created=False)
        await conn.execute(
            experiment_outcomes.insert().values(
                experiment_invocation_id=experiment_invocation_id,
                result=result_json,
                receipts=receipts_json,
                image=image,
                snapshot_sha256=snapshot,
                accepted_attempt_id=attempt_id,
                accepted_attempt_no=attempt_no,
            )
        )
        return Acceptance(created=True)


# --- reference resolution (DB-backed) ------------------------------------------


async def load_experiment_outcome(
    engine: AsyncEngine, experiment_invocation_id: uuid.UUID
) -> ExperimentOutcome | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(experiment_outcomes).where(
                    experiment_outcomes.c.experiment_invocation_id
                    == experiment_invocation_id
                )
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    return ExperimentOutcome(
        experiment_invocation_id=experiment_invocation_id,
        result=ExperimentResult.model_validate(row.result),
        receipts=tuple(
            ExperimentComputationReceipt.model_validate(r) for r in row.receipts
        ),
        image=row.image,
        snapshot_sha256=row.snapshot_sha256,
        accepted_attempt_id=row.accepted_attempt_id,
        accepted_attempt_no=row.accepted_attempt_no,
    )


async def resolve_experiment_artifact(
    engine: AsyncEngine, locator: str
) -> ResolvedExperimentArtifact:
    """Resolve an experiment artifact locator against the ACCEPTED outcome
    (an unaccepted experiment has no citable evidence)."""
    try:
        experiment_id, _, _ = parse_experiment_locator(locator)
    except ValueError as exc:
        raise ExperimentReferenceError(f"invalid experiment locator: {exc}") from exc
    outcome = await load_experiment_outcome(engine, experiment_id)
    if outcome is None:
        raise ExperimentReferenceError(
            f"experiment {experiment_id} has no accepted outcome"
        )
    return resolve_experiment_reference(
        outcome_receipts=outcome.receipts,
        outcome_experiment_id=experiment_id,
        locator=locator,
    )
