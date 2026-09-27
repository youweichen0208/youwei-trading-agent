"""Atomic forecast sealing (S05, time-protocol §4 + architecture §3.4/§6).

Ledger.seal(case, release, source_results, provenance, attempt_token):

1. clock-skew guard: sealing stops when the app clock drifts from the
   database clock beyond the configured threshold.
2. one short transaction: lock the campaign's chain head, read
   clock_timestamp() (the real-time seal checkpoint, never the
   transaction-start now()), re-check the case window
   (decision_cutoff <= sealed_at <= prediction_deadline — a seal that
   waited on the lock past the deadline is rejected, not backdated),
   fence the producing attempt, validate the three source positions,
   then insert commit + predictions + chain advance together.
3. a second short transaction appends the durable confirmation with
   its own clock_timestamp() and the timeliness judgment. A crash
   between the two leaves the commit unconfirmed — conservatively
   'uncertain' once the deadline passes, never silently on_time.

Idempotency: (case, release) is unique; a replay with identical
business payload returns the original commit. A confirmed commit is
never re-judged; an unconfirmed one may be reconciled by a replay,
with timeliness decided by the reconciliation clock (late if the
deadline has since passed). Different payload for the same (case,
release) is a conflict.

The chain: per-campaign hash chain with a monotonic sequence, fixed
canonical row content and the head locked in the sealing transaction
(architecture §6). verify_chain() recomputes every content hash from
the stored rows.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import clock_skew_seconds
from youwei_core.db.meta import (
    SOURCE_STATUSES,
    attempts,
    campaigns,
    events,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    jobs,
    ledger_chains,
    predictions,
    research_releases,
    snapshots,
)
from youwei_core.ledger.service import (
    GENESIS_HASH,
    PHASE1A_ENABLED_SOURCES,
    decimal_str,
    sha256_hex,
)

DEFAULT_MAX_CLOCK_SKEW_SECONDS = 5.0
NUMERIC_SCALE = Decimal("0.0000000001")  # 1e-10, the column scale


class SealError(Exception):
    pass


class EarlySeal(SealError):
    """Sealing before the decision cutoff."""


class LateSeal(SealError):
    """Sealing after the prediction deadline (rejected, never backdated)."""


class ClockSkewExceeded(SealError):
    pass


class FencedSeal(SealError):
    """The producing attempt is no longer current; the business result
    is not applied."""


class SealConflict(SealError):
    """Same (case, release) already sealed with different content."""


class SourceValidationError(SealError):
    pass


class CaseNotFound(SealError):
    pass


class ReleaseMismatch(SealError):
    pass


class EvidenceViolation(SealError):
    """A referenced evidence snapshot breaks point-in-time discipline:
    frozen after the case's decision cutoff, or not a forward view."""


class SourcePrediction(BaseModel):
    source: str
    source_status: str
    reason: str | None = None
    p_outperform: float | None = None
    expected_excess_return: float | None = None
    evidence_snapshot_id: uuid.UUID | None = None
    model_version: str | None = None


class SealRequest(BaseModel):
    case_id: uuid.UUID
    release_id: str  # release slug; must be the campaign's release
    sources: list[SourcePrediction] = Field(min_length=1)
    input_manifest: dict = Field(default_factory=dict)
    attempt_id: uuid.UUID
    attempt_no: int = Field(ge=1)


@dataclass
class SealResult:
    commit_id: uuid.UUID
    created: bool
    chain_seq: int
    sealed_at: datetime
    confirmed_at: datetime | None
    timeliness: str  # on_time | late | unconfirmed


# --- canonical content ------------------------------------------------------


def _decimal(value, field: str) -> Decimal | None:
    """Canonical numeric representation: finite Decimal quantized to
    the column scale, so the hash computed at seal time matches a
    recompute from the stored row."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SourceValidationError(f"{field} is not a number: {value!r}") from exc
    if not d.is_finite():
        raise SourceValidationError(f"{field} must be finite, got {value!r}")
    return d.quantize(NUMERIC_SCALE)


def _prediction_entry(pred: SourcePrediction) -> dict:
    p = _decimal(pred.p_outperform, "p_outperform")
    e = _decimal(pred.expected_excess_return, "expected_excess_return")
    return {
        "source": pred.source,
        "source_status": pred.source_status,
        "reason": pred.reason,
        "p_outperform": None if p is None else decimal_str(p),
        "expected_excess_return": None if e is None else decimal_str(e),
        "evidence_snapshot_id": (
            None if pred.evidence_snapshot_id is None else str(pred.evidence_snapshot_id)
        ),
        "model_version": pred.model_version,
    }


def _validate_sources(sources: list[SourcePrediction], campaign) -> list[dict]:
    """campaign-policy §3 value discipline. Returns canonical entries."""
    enabled = set(campaign.enabled_sources)
    if enabled != set(PHASE1A_ENABLED_SOURCES):
        # Phase 1A is the only registered policy; anything else is a
        # registration the ledger does not know how to enforce yet.
        raise SourceValidationError(
            f"campaign enabled_sources {sorted(enabled)} have no registered "
            "sealing policy (Phase 1A only)"
        )

    by_source = {}
    for pred in sources:
        if pred.source in by_source:
            raise SourceValidationError(f"duplicate position for source {pred.source!r}")
        by_source[pred.source] = pred
    if set(by_source) != {"baseline", "quant_model", "llm_adjusted"}:
        raise SourceValidationError(
            "a commit must seal all three source positions "
            "(baseline, quant_model, llm_adjusted); partial submits are rejected"
        )

    entries = []
    for source, pred in by_source.items():
        if pred.source_status not in SOURCE_STATUSES:
            raise SourceValidationError(f"invalid source_status {pred.source_status!r}")

        if pred.source_status == "unavailable":
            if pred.p_outperform is not None or pred.expected_excess_return is not None:
                raise SourceValidationError(
                    f"{source}: unavailable positions carry no values "
                    "(never a fake probability)"
                )
            if not pred.reason:
                raise SourceValidationError(f"{source}: unavailable needs a reason")
        else:
            p = _decimal(pred.p_outperform, "p_outperform")
            if p is None:
                raise SourceValidationError(f"{source}: {pred.source_status} needs p_outperform")
            if not (Decimal(0) <= p <= Decimal(1)):
                raise SourceValidationError(
                    f"{source}: p_outperform must be within [0, 1], got {p}"
                )
            _decimal(pred.expected_excess_return, "expected_excess_return")

        if pred.source_status == "fallback":
            if campaign.fallback_policy != "phase1b-llm-from-quant":
                raise SourceValidationError(
                    f"{source}: fallback is not allowed by policy "
                    f"{campaign.fallback_policy!r} (Phase 1A has no fallback)"
                )

        if source == "llm_adjusted" and "llm_adjusted" not in enabled:
            if pred.source_status != "unavailable" or pred.reason != "not_enabled":
                raise SourceValidationError(
                    "Phase 1A llm_adjusted position is fixed: unavailable with "
                    "reason=not_enabled"
                )

        entries.append(_prediction_entry(pred))
    entries.sort(key=lambda e: e["source"])
    return entries


def _payload_hash(case_id, release_id: str, input_manifest: dict, entries: list[dict]) -> str:
    return sha256_hex(
        {
            "case_id": str(case_id),
            "release_id": release_id,
            "input_manifest": input_manifest,
            "predictions": entries,
        }
    )


def _content_hash(
    *,
    case_id,
    release_id: str,
    chain_id: str,
    chain_seq: int,
    prev_hash: str,
    sealed_at: datetime,
    attempt_id,
    attempt_no,
    input_manifest: dict,
    entries: list[dict],
) -> str:
    return sha256_hex(
        {
            "case_id": str(case_id),
            "release_id": release_id,
            "chain_id": chain_id,
            "chain_seq": chain_seq,
            "prev_hash": prev_hash,
            "sealed_at": sealed_at.isoformat(),
            "attempt_id": None if attempt_id is None else str(attempt_id),
            "attempt_no": attempt_no,
            "input_manifest": input_manifest,
            "predictions": entries,
        }
    )


# --- sealing -----------------------------------------------------------------


async def seal_commit(
    engine: AsyncEngine,
    request: SealRequest,
    *,
    max_clock_skew_seconds: float = DEFAULT_MAX_CLOCK_SKEW_SECONDS,
    confirm: bool = True,
) -> SealResult:
    """Seal one case's three source positions atomically.

    confirm=False leaves the durable confirmation unwritten (crash
    window / reconciliation testing): the commit exists but stays
    unconfirmed until a replay reconciles it."""
    skew = await clock_skew_seconds(engine)
    if skew > max_clock_skew_seconds:
        raise ClockSkewExceeded(
            f"app/db clock skew {skew:.3f}s exceeds {max_clock_skew_seconds}s; "
            "formal sealing stops (time-protocol §5)"
        )

    # transaction A: chain lock -> clock -> window/fence checks -> insert
    async with engine.begin() as conn:
        case = (
            await conn.execute(
                select(forecast_cases).where(forecast_cases.c.id == request.case_id)
            )
        ).mappings().one_or_none()
        if case is None:
            raise CaseNotFound(str(request.case_id))
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == case.campaign_id)
            )
        ).mappings().one()
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.release_id == request.release_id
                )
            )
        ).mappings().one_or_none()
        if release is None or release.id != campaign.release_row_id:
            raise ReleaseMismatch(
                f"release {request.release_id!r} is not the campaign's release"
            )

        chain = (
            await conn.execute(
                select(ledger_chains)
                .where(ledger_chains.c.chain_id == f"campaign:{campaign.id}")
                .with_for_update()
            )
        ).mappings().one()

        entries = _validate_sources(request.sources, campaign)
        payload_sha = _payload_hash(
            request.case_id, request.release_id, request.input_manifest, entries
        )

        existing = (
            await conn.execute(
                select(forecast_commits).where(
                    forecast_commits.c.case_id == request.case_id,
                    forecast_commits.c.release_row_id == release.id,
                )
            )
        ).mappings().first()
        if existing is not None:
            if existing.payload_sha256 != payload_sha:
                raise SealConflict(
                    f"case {request.case_id} already sealed for release "
                    f"{request.release_id!r} with different content"
                )
            commit_id = existing.id
            sealed_at = existing.sealed_at
            chain_seq = existing.chain_seq
            created = False
        else:
            # fence the producing attempt before any business write
            attempt = (
                await conn.execute(
                    select(
                        attempts.c.status,
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
                        attempts.c.id == request.attempt_id,
                        attempts.c.attempt_no == request.attempt_no,
                    )
                )
            ).mappings().one_or_none()
            db_now = (await conn.execute(select(func.clock_timestamp()))).scalar_one()
            if (
                attempt is None
                or attempt.attempt_count != request.attempt_no
                or attempt.job_status != "running"
                or attempt.status != "running"
                or attempt.lease_expires_at <= db_now
            ):
                raise FencedSeal(
                    f"attempt {request.attempt_no} is not the job's current "
                    "running attempt; result not applied"
                )
            if attempt.tenant_id != campaign.tenant_id:
                raise FencedSeal(
                    "attempt belongs to a different tenant than the case's campaign"
                )

            sealed_at = db_now
            if sealed_at < case.decision_cutoff_utc:
                raise EarlySeal(
                    f"sealed_at {sealed_at.isoformat()} is before decision cutoff "
                    f"{case.decision_cutoff_utc.isoformat()}"
                )
            if sealed_at > case.prediction_deadline_utc:
                raise LateSeal(
                    f"sealed_at {sealed_at.isoformat()} is past prediction deadline "
                    f"{case.prediction_deadline_utc.isoformat()}; new predictions "
                    "are rejected after the deadline"
                )

            # evidence discipline, checked at the write boundary (after
            # fencing and window checks — a fenced or late attempt is
            # rejected regardless of its payload): produced positions
            # of evidence-consuming models must reference the frozen
            # snapshot they were computed from, and every referenced
            # snapshot must be a forward view frozen at or before this
            # case's decision cutoff (time-protocol §2; S04 acceptance:
            # any model input traces to data frozen at or before the
            # cutoff). Idempotent replays skip this — their content
            # already passed here when first sealed.
            for pred in request.sources:
                if (
                    pred.source in ("quant_model", "llm_adjusted")
                    and pred.source_status == "produced"
                    and pred.evidence_snapshot_id is None
                ):
                    raise SourceValidationError(
                        f"{pred.source}: produced positions must reference the "
                        "frozen evidence they were computed from (untraceable "
                        "model inputs never enter the ledger)"
                    )
            for evidence_id in {
                p.evidence_snapshot_id
                for p in request.sources
                if p.evidence_snapshot_id is not None
            }:
                snap = (
                    await conn.execute(
                        select(snapshots).where(snapshots.c.id == evidence_id)
                    )
                ).mappings().one_or_none()
                if snap is None:
                    raise EvidenceViolation(
                        f"evidence snapshot {evidence_id} does not exist"
                    )
                if snap.mode != "forward":
                    raise EvidenceViolation(
                        f"evidence snapshot {evidence_id} is mode={snap.mode!r}; "
                        "formal predictions may only consume forward views "
                        "(historical_source is a reconstruction, never evidence)"
                    )
                if snap.as_of > case.decision_cutoff_utc:
                    raise EvidenceViolation(
                        f"evidence snapshot {evidence_id} was frozen at "
                        f"{snap.as_of.isoformat()}, after the case cutoff "
                        f"{case.decision_cutoff_utc.isoformat()}; post-cutoff "
                        "evidence cannot enter the ledger"
                    )

            chain_seq = chain.head_seq + 1
            content_sha = _content_hash(
                case_id=request.case_id,
                release_id=request.release_id,
                chain_id=chain.chain_id,
                chain_seq=chain_seq,
                prev_hash=chain.head_hash,
                sealed_at=sealed_at,
                attempt_id=request.attempt_id,
                attempt_no=request.attempt_no,
                input_manifest=request.input_manifest,
                entries=entries,
            )
            commit_id = uuid.uuid4()
            await conn.execute(
                forecast_commits.insert().values(
                    id=commit_id,
                    case_id=request.case_id,
                    release_row_id=release.id,
                    chain_id=chain.chain_id,
                    chain_seq=chain_seq,
                    prev_hash=chain.head_hash,
                    payload_sha256=payload_sha,
                    content_sha256=content_sha,
                    sealed_at=sealed_at,
                    attempt_id=request.attempt_id,
                    attempt_no=request.attempt_no,
                    input_manifest=request.input_manifest,
                )
            )
            for entry in entries:
                await conn.execute(
                    predictions.insert().values(
                        id=uuid.uuid4(),
                        commit_id=commit_id,
                        source=entry["source"],
                        source_status=entry["source_status"],
                        reason=entry["reason"],
                        p_outperform=(
                            None
                            if entry["p_outperform"] is None
                            else Decimal(entry["p_outperform"])
                        ),
                        expected_excess_return=(
                            None
                            if entry["expected_excess_return"] is None
                            else Decimal(entry["expected_excess_return"])
                        ),
                        evidence_snapshot_id=(
                            None
                            if entry["evidence_snapshot_id"] is None
                            else uuid.UUID(entry["evidence_snapshot_id"])
                        ),
                        model_version=entry["model_version"],
                    )
                )
            await conn.execute(
                forecast_commit_events.insert().values(
                    id=uuid.uuid4(),
                    commit_id=commit_id,
                    event_type="sealed",
                    payload={
                        "sealed_at": sealed_at.isoformat(),
                        "chain_seq": chain_seq,
                        "attempt_no": request.attempt_no,
                    },
                )
            )
            await conn.execute(
                ledger_chains.update()
                .where(ledger_chains.c.chain_id == chain.chain_id)
                .values(head_seq=chain_seq, head_hash=content_sha, updated_at=func.now())
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=campaign.tenant_id,
                    run_id=attempt.run_id,
                    job_id=attempt.job_id,
                    event_type="ledger.commit_sealed",
                    payload={
                        "commit_id": str(commit_id),
                        "case_id": str(request.case_id),
                        "chain_seq": chain_seq,
                    },
                )
            )
            created = True

    # transaction B: durable confirmation with its own real-time clock
    confirmed_at = None
    timeliness = "unconfirmed"
    if confirm:
        confirmed_at, timeliness = await _confirm(engine, commit_id, case, campaign.tenant_id)
    return SealResult(
        commit_id=commit_id,
        created=created,
        chain_seq=chain_seq,
        sealed_at=sealed_at,
        confirmed_at=confirmed_at,
        timeliness=timeliness,
    )


async def _confirm(
    engine: AsyncEngine, commit_id, case, tenant_id
) -> tuple[datetime, str]:
    """Append the durable confirmation and its timeliness judgment.
    At most one per commit (partial unique index); a replay after a
    crash between A and B lands here exactly once."""
    async with engine.begin() as conn:
        confirmed = (
            await conn.execute(
                select(forecast_commit_events.c.payload).where(
                    forecast_commit_events.c.commit_id == commit_id,
                    forecast_commit_events.c.event_type == "durable_confirmation",
                )
            )
        ).scalars().first()
        if confirmed is not None:
            return (
                datetime.fromisoformat(confirmed["confirmed_at"]),
                confirmed["timeliness"],
            )
        confirmed_at = (
            await conn.execute(select(func.clock_timestamp()))
        ).scalar_one()
        timeliness = (
            "on_time" if confirmed_at <= case.prediction_deadline_utc else "late"
        )
        await conn.execute(
            forecast_commit_events.insert().values(
                id=uuid.uuid4(),
                commit_id=commit_id,
                event_type="durable_confirmation",
                payload={
                    "confirmed_at": confirmed_at.isoformat(),
                    "timeliness": timeliness,
                    "prediction_deadline": case.prediction_deadline_utc.isoformat(),
                },
            )
        )
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                event_type="ledger.commit_confirmed",
                payload={
                    "commit_id": str(commit_id),
                    "timeliness": timeliness,
                },
            )
        )
    return confirmed_at, timeliness


# --- queries ------------------------------------------------------------------


async def commit_status(engine: AsyncEngine, commit_id: uuid.UUID) -> dict:
    """Commit + predictions + derived timeliness.

    time-protocol §4: no confirmation -> unconfirmed, and conservatively
    'uncertain' once the deadline has passed; a confirmation is never
    upgraded by later events."""
    async with engine.begin() as conn:
        commit = (
            await conn.execute(
                select(forecast_commits).where(forecast_commits.c.id == commit_id)
            )
        ).mappings().one_or_none()
        if commit is None:
            raise CaseNotFound(f"commit {commit_id} not found")
        case = (
            await conn.execute(
                select(forecast_cases).where(forecast_cases.c.id == commit.case_id)
            )
        ).mappings().one()
        preds = (
            await conn.execute(
                select(predictions).where(predictions.c.commit_id == commit_id)
                .order_by(predictions.c.source)
            )
        ).mappings().all()
        confirmation = (
            await conn.execute(
                select(forecast_commit_events.c.payload)
                .where(
                    forecast_commit_events.c.commit_id == commit_id,
                    forecast_commit_events.c.event_type == "durable_confirmation",
                )
                .order_by(forecast_commit_events.c.occurred_at.desc())
                .limit(1)
            )
        ).scalars().first()

    if confirmation is not None:
        timeliness = confirmation["timeliness"]
        confirmed_at = confirmation["confirmed_at"]
    else:
        confirmed_at = None
        db_now = await _db_now(engine)
        timeliness = (
            "unconfirmed" if db_now <= case.prediction_deadline_utc else "uncertain"
        )
    return {
        "commit_id": str(commit_id),
        "case_id": str(commit.case_id),
        "chain_id": commit.chain_id,
        "chain_seq": commit.chain_seq,
        "content_sha256": commit.content_sha256,
        "sealed_at": commit.sealed_at.isoformat(),
        "confirmed_at": confirmed_at,
        "timeliness": timeliness,
        "predictions": [
            {
                "source": p.source,
                "source_status": p.source_status,
                "reason": p.reason,
                "p_outperform": None if p.p_outperform is None else decimal_str(p.p_outperform),
                "expected_excess_return": (
                    None if p.expected_excess_return is None else decimal_str(p.expected_excess_return)
                ),
            }
            for p in preds
        ],
    }


async def _db_now(engine: AsyncEngine) -> datetime:
    async with engine.begin() as conn:
        return (await conn.execute(select(func.now()))).scalar_one()


async def verify_chain(engine: AsyncEngine, chain_id: str) -> dict:
    """Recompute every commit's content hash from the stored rows and
    check linkage: seq contiguous from 1, prev_hash chaining, head row
    matching the last commit. Tampering anywhere breaks the recompute
    or the link."""
    async with engine.begin() as conn:
        chain = (
            await conn.execute(
                select(ledger_chains).where(ledger_chains.c.chain_id == chain_id)
            )
        ).mappings().one_or_none()
        if chain is None:
            raise CaseNotFound(f"chain {chain_id!r} not found")
        commits = (
            await conn.execute(
                select(forecast_commits)
                .where(forecast_commits.c.chain_id == chain_id)
                .order_by(forecast_commits.c.chain_seq)
            )
        ).mappings().all()
        preds_by_commit = {}
        if commits:
            rows = (
                await conn.execute(
                    select(predictions)
                    .where(
                        predictions.c.commit_id.in_([c.id for c in commits])
                    )
                    .order_by(predictions.c.commit_id, predictions.c.source)
                )
            ).mappings().all()
            for p in rows:
                preds_by_commit.setdefault(p.commit_id, []).append(p)
        release_ids = {
            r.id: r.release_id
            for r in (
                await conn.execute(select(research_releases))
            ).mappings().all()
        }

        issues = []
        prev_hash = None
        for i, commit in enumerate(commits):
            expected_seq = i + 1
            if commit.chain_seq != expected_seq:
                issues.append(
                    f"seq gap/dup at position {i}: {commit.chain_seq} != {expected_seq}"
                )
            if i == 0 and commit.prev_hash != GENESIS_HASH:
                issues.append("first commit does not chain from the genesis hash")
            entries = [
                {
                    "source": p.source,
                    "source_status": p.source_status,
                    "reason": p.reason,
                    "p_outperform": None if p.p_outperform is None else decimal_str(p.p_outperform),
                    "expected_excess_return": (
                        None if p.expected_excess_return is None else decimal_str(p.expected_excess_return)
                    ),
                    "evidence_snapshot_id": (
                        None if p.evidence_snapshot_id is None else str(p.evidence_snapshot_id)
                    ),
                    "model_version": p.model_version,
                }
                for p in preds_by_commit.get(commit.id, [])
            ]
            recomputed = _content_hash(
                case_id=commit.case_id,
                release_id=release_ids[commit.release_row_id],
                chain_id=commit.chain_id,
                chain_seq=commit.chain_seq,
                prev_hash=commit.prev_hash,
                sealed_at=commit.sealed_at,
                attempt_id=commit.attempt_id,
                attempt_no=commit.attempt_no,
                input_manifest=commit.input_manifest,
                entries=entries,
            )
            if recomputed != commit.content_sha256:
                issues.append(
                    f"commit {commit.id} content hash mismatch: stored "
                    f"{commit.content_sha256} != recomputed {recomputed}"
                )
            if i > 0 and commit.prev_hash != prev_hash:
                issues.append(
                    f"commit {commit.id} prev_hash does not chain to the "
                    "previous commit's content hash"
                )
            prev_hash = commit.content_sha256

        if chain.head_seq != len(commits):
            issues.append(
                f"chain head_seq {chain.head_seq} != commit count {len(commits)}"
            )
        if commits and chain.head_hash != commits[-1].content_sha256:
            issues.append("chain head_hash does not match the last commit")
        if not commits and chain.head_hash != GENESIS_HASH:
            issues.append("empty chain head_hash is not the genesis hash")

    return {
        "chain_id": chain_id,
        "ok": not issues,
        "issues": issues,
        "head_seq": chain.head_seq,
        "commit_count": len(commits),
    }
