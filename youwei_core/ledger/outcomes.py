"""Outcome resolution and append-only revisions (S05, target-spec §4–§5).

Every status assignment is itself a versioned fact: resolve_outcome
computes realized returns from a frozen snapshot of the holding
window and appends an OutcomeRevision; later arriving data or vendor
corrections append new revisions that supersede the previous head.
Nothing is updated or deleted, and forks are impossible (unique
(case_id, revision) + supersedes-always-head under the case lock).

Return convention (target-spec §2–§3, resolver total-return-v1):
- entry at the entry session OPEN, exit at the exit session CLOSE;
  the entry day counts as D1
- wealth per 1 unit invested: units = 1 / entry_open; for each
  trading day AFTER the entry day apply split_factor first, then
  reinvest that day's cash dividend at that day's close
  (units *= 1 + div/close); ex-date == entry day pays nothing (not
  entitled), ex-date == exit day still counts (held to that close)
- asset_return = units * exit_close - 1; the benchmark uses the same
  convention; excess_return = asset - benchmark, all quantized to
  the column scale (1e-10)

Data policy:
- resolution uses the PIT view as of the database now (time-protocol
  §6: outcomes may use data arriving during and after the window,
  written only as new revisions)
- completeness requires a quality-ok bar with valid prices for every
  trading day of [entry_date, exit_date] for BOTH securities — a
  mid-window hole means corporate actions cannot be verified
- within the grace period (5 trading days after the planned exit,
  target-spec §4) incomplete data is 'pending': nothing is written;
  after it, unresolved is recorded with the missing pieces
- unscorable (no valid price formed at the original entry session)
  is an evidence-driven fact, never inferred from missing data:
  record_unscorable appends it with reason + evidence reference
"""

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import next_trading_day, session_times
from youwei_core.data.pit import daily_bars_asof
from youwei_core.data.snapshots import freeze_daily_bars, read_snapshot
from youwei_core.db.meta import (
    calendar_days,
    campaigns,
    events,
    forecast_cases,
    outcome_revisions,
)
from youwei_core.ledger.service import decimal_str

RESOLVER_VERSION = "total-return-v1"
GRACE_TRADING_DAYS = 5
NUMERIC_SCALE = Decimal("0.0000000001")


class OutcomeError(Exception):
    pass


class CaseNotFound(OutcomeError):
    pass


class NotYetMature(OutcomeError):
    """Resolution starts only after the planned exit (target-spec §5)."""


class InvalidOutcomeState(OutcomeError):
    pass


@dataclass
class OutcomeAttempt:
    case_id: uuid.UUID
    status: str  # resolved | unresolved | pending | unscorable
    created: bool
    outcome_id: uuid.UUID | None
    revision: int | None
    asset_return: Decimal | None
    benchmark_return: Decimal | None
    excess_return: Decimal | None
    snapshot_id: uuid.UUID | None
    grace_deadline_utc: datetime
    missing: list


# --- computation --------------------------------------------------------------


def _dec(x) -> Decimal:
    return Decimal(str(x))


def _bar_valid(bar: dict) -> bool:
    return (
        bar.get("quality") == "ok"
        and _dec(bar["open"]) > 0
        and _dec(bar["close"]) > 0
        and _dec(bar["split_factor"]) > 0
    )


def total_return_from_bars(bars: list[dict]) -> Decimal:
    """Wealth per 1 unit invested at the first bar's open, exited at
    the last bar's close, with splits and ex-date-close dividend
    reinvestment in between. Bars are one security's window, sorted
    by trade_date and pre-validated."""
    if not bars:
        raise OutcomeError("no bars to compute a return from")
    units = Decimal(1) / _dec(bars[0]["open"])
    for bar in bars[1:]:
        split = _dec(bar["split_factor"])
        if split != 1:
            units *= split
        div = _dec(bar["div_cash"])
        if div > 0:
            units *= 1 + div / _dec(bar["close"])
    return (units * _dec(bars[-1]["close"]) - 1).quantize(NUMERIC_SCALE)


# --- calendar helpers ----------------------------------------------------------


async def _trading_days_between(engine: AsyncEngine, start: date, end: date) -> list[date]:
    async with engine.begin() as conn:
        return list(
            (
                await conn.execute(
                    select(calendar_days.c.date)
                    .where(
                        calendar_days.c.venue == "XNYS",
                        calendar_days.c.is_trading.is_(True),
                        calendar_days.c.date >= start,
                        calendar_days.c.date <= end,
                    )
                    .order_by(calendar_days.c.date)
                )
            ).scalars().all()
        )


async def grace_deadline(engine: AsyncEngine, exit_date: date) -> datetime:
    """Close of the 5th trading day after the planned exit
    (target-spec §4: the uniform resolution grace period)."""
    d = await next_trading_day(engine, exit_date, n=GRACE_TRADING_DAYS)
    s = await session_times(engine, d)
    return s.close_utc


# --- resolution -----------------------------------------------------------------


async def resolve_outcome(
    engine: AsyncEngine,
    case_id: uuid.UUID,
    *,
    correction_reason: str | None = None,
) -> OutcomeAttempt:
    """Attempt to resolve (or re-resolve) one case's outcome.

    - complete data                -> resolved revision (idempotent if
                                      unchanged: same frozen content
                                      + same values)
    - incomplete, within grace     -> pending, nothing written
    - incomplete, grace expired    -> unresolved revision with the
                                      missing pieces in basis
    - earlier revision exists      -> new revision supersedes the head
    """
    async with engine.begin() as conn:
        case = (
            await conn.execute(
                select(forecast_cases).where(forecast_cases.c.id == case_id)
            )
        ).mappings().one_or_none()
        if case is None:
            raise CaseNotFound(str(case_id))
        db_now = (await conn.execute(select(func.now()))).scalar_one()

    if db_now < case.exit_at_utc:
        raise NotYetMature(
            f"case exit {case.exit_at_utc.isoformat()} is in the future; "
            "resolution starts after the planned exit"
        )

    entry_date = case.entry_at_utc.date()
    exit_date = case.exit_at_utc.date()
    trading_days = await _trading_days_between(engine, entry_date, exit_date)
    if not trading_days:
        raise OutcomeError(
            f"no trading days in [{entry_date}, {exit_date}]: the calendar "
            "range was never built"
        )
    deadline = await grace_deadline(engine, exit_date)

    bars = await daily_bars_asof(
        engine,
        [case.security_id, case.benchmark_security_id],
        entry_date,
        exit_date,
        as_of=db_now,
        mode="forward",
    )
    by_security: dict[str, dict[str, dict]] = {}
    for bar in bars:
        by_security.setdefault(bar["security_id"], {})[bar["trade_date"]] = bar

    missing: list = []
    for sec_label, sec_id in (
        ("security", case.security_id),
        ("benchmark", case.benchmark_security_id),
    ):
        sec_bars = by_security.get(str(sec_id), {})
        for d in trading_days:
            bar = sec_bars.get(d.isoformat())
            if bar is None or not _bar_valid(bar):
                entry = {"security": sec_label, "trade_date": d.isoformat()}
                if bar is not None:
                    entry["reason"] = "invalid_bar"
                missing.append(entry)

    # freeze the evidence of what the resolver saw — only when a
    # revision will actually be written: a pending attempt writes
    # nothing (the scheduler retries later), so it freezes nothing.
    # The snapshot query embeds the resolution-time as_of; identity
    # therefore cannot dedup across attempts — idempotency compares
    # the frozen CONTENT hash instead (same data -> same content).
    if missing:
        if db_now <= deadline:
            return OutcomeAttempt(
                case_id=case_id,
                status="pending",
                created=False,
                outcome_id=None,
                revision=None,
                asset_return=None,
                benchmark_return=None,
                excess_return=None,
                snapshot_id=None,
                grace_deadline_utc=deadline,
                missing=missing,
            )
        snap = await freeze_daily_bars(
            engine,
            [case.security_id, case.benchmark_security_id],
            entry_date,
            exit_date,
            as_of=db_now,
            mode="forward",
        )
        return await _append_revision(
            engine,
            case,
            status="unresolved",
            snapshot_id=snap.snapshot_id,
            snapshot_content_sha=snap.content_sha256,
            values=None,
            basis={
                "missing": missing,
                "grace_deadline_utc": deadline.isoformat(),
                "trading_days_expected": len(trading_days),
            },
            correction_reason=correction_reason,
            event_status="outcome.unresolved",
        )

    snap = await freeze_daily_bars(
        engine,
        [case.security_id, case.benchmark_security_id],
        entry_date,
        exit_date,
        as_of=db_now,
        mode="forward",
    )
    # compute from the frozen snapshot content, so the stored numbers
    # are exactly recomputable from the referenced snapshot
    frozen = await read_snapshot(engine, snap.snapshot_id)
    frozen_bars = json.loads(frozen["content"])
    grouped: dict[str, list[dict]] = {}
    for bar in frozen_bars:
        grouped.setdefault(bar["security_id"], []).append(bar)
    asset_bars = sorted(grouped[str(case.security_id)], key=lambda b: b["trade_date"])
    bench_bars = sorted(
        grouped[str(case.benchmark_security_id)], key=lambda b: b["trade_date"]
    )
    asset_return = total_return_from_bars(asset_bars)
    benchmark_return = total_return_from_bars(bench_bars)
    excess_return = (asset_return - benchmark_return).quantize(NUMERIC_SCALE)

    return await _append_revision(
        engine,
        case,
        status="resolved",
        snapshot_id=snap.snapshot_id,
        snapshot_content_sha=snap.content_sha256,
        values=(asset_return, benchmark_return, excess_return),
        basis={
            "grace_deadline_utc": deadline.isoformat(),
            "trading_days": len(trading_days),
            "computed_from_snapshot": True,
        },
        correction_reason=correction_reason,
        event_status="outcome.resolved",
    )


async def record_unscorable(
    engine: AsyncEngine,
    case_id: uuid.UUID,
    *,
    reason: str,
    evidence_ref: str,
    correction_reason: str | None = None,
) -> OutcomeAttempt:
    """Append an unscorable revision: the original entry session
    formed no valid price (halt / no trade) — a market fact backed by
    evidence, never inferred from vendor data being late."""
    if not reason or not evidence_ref:
        raise OutcomeError("unscorable requires a reason and an evidence reference")
    async with engine.begin() as conn:
        case = (
            await conn.execute(
                select(forecast_cases).where(forecast_cases.c.id == case_id)
            )
        ).mappings().one_or_none()
        if case is None:
            raise CaseNotFound(str(case_id))
    return await _append_revision(
        engine,
        case,
        status="unscorable",
        snapshot_id=None,
        snapshot_content_sha=None,
        values=None,
        basis={"reason": reason, "evidence_ref": evidence_ref},
        correction_reason=correction_reason,
        event_status="outcome.unscorable",
    )


# --- revision chain --------------------------------------------------------------


async def _append_revision(
    engine: AsyncEngine,
    case,
    *,
    status: str,
    snapshot_id,
    snapshot_content_sha: str | None,
    values,
    basis: dict,
    correction_reason: str | None,
    event_status: str,
) -> OutcomeAttempt:
    """One short transaction: lock the case row (serializes revisions
    per case), check the head, append. Idempotent when the head already
    records the same fact (same status, same frozen evidence content,
    same values); forks impossible by construction."""
    stored_basis = dict(basis)
    if snapshot_content_sha is not None:
        stored_basis["snapshot_content_sha256"] = snapshot_content_sha
    async with engine.begin() as conn:
        # lock the case row: serializes revision appends per case
        await conn.execute(
            select(forecast_cases.c.id)
            .where(forecast_cases.c.id == case.id)
            .with_for_update()
        )
        current = (
            await conn.execute(
                select(outcome_revisions)
                .where(outcome_revisions.c.case_id == case.id)
                .order_by(outcome_revisions.c.revision.desc())
                .limit(1)
            )
        ).mappings().first()

        if current is not None:
            if current.status == "unscorable" and status != "unscorable":
                raise InvalidOutcomeState(
                    "case head is unscorable: that is a permanent entry-session "
                    "fact; changing it requires an explicit evidence revision"
                )
            same_fact = (
                current.status == status
                and (
                    status == "unscorable"
                    or (
                        current.basis.get("snapshot_content_sha256")
                        == snapshot_content_sha
                        and (
                            status != "resolved"
                            or (
                                current.asset_return == values[0]
                                and current.benchmark_return == values[1]
                                and current.excess_return == values[2]
                            )
                        )
                    )
                )
                and (status != "unscorable" or current.basis.get("reason") == basis.get("reason"))
            )
            if same_fact:
                return OutcomeAttempt(
                    case_id=case.id,
                    status=current.status,
                    created=False,
                    outcome_id=current.id,
                    revision=current.revision,
                    asset_return=current.asset_return,
                    benchmark_return=current.benchmark_return,
                    excess_return=current.excess_return,
                    snapshot_id=current.prices_and_actions_snapshot_id,
                    grace_deadline_utc=None,
                    missing=[],
                )
            revision = current.revision + 1
            supersedes = current.id
        else:
            revision = 1
            supersedes = None

        reason = correction_reason
        if revision > 1 and not reason:
            reason = "data_revision"

        outcome_id = uuid.uuid4()
        asset_return, benchmark_return, excess_return = (
            values if values is not None else (None, None, None)
        )
        await conn.execute(
            outcome_revisions.insert().values(
                id=outcome_id,
                case_id=case.id,
                revision=revision,
                supersedes_outcome_id=supersedes,
                status=status,
                entry_at_utc=case.entry_at_utc,
                exit_at_utc=case.exit_at_utc,
                asset_return=asset_return,
                benchmark_return=benchmark_return,
                excess_return=excess_return,
                prices_and_actions_snapshot_id=snapshot_id,
                resolver_version=RESOLVER_VERSION,
                correction_reason=reason,
                basis=stored_basis,
            )
        )
        tenant_id = (
            await conn.execute(
                select(campaigns.c.tenant_id).where(campaigns.c.id == case.campaign_id)
            )
        ).scalar_one()
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                event_type=event_status,
                payload={
                    "case_id": str(case.id),
                    "outcome_id": str(outcome_id),
                    "revision": revision,
                },
            )
        )
    return OutcomeAttempt(
        case_id=case.id,
        status=status,
        created=True,
        outcome_id=outcome_id,
        revision=revision,
        asset_return=asset_return,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        snapshot_id=snapshot_id,
        grace_deadline_utc=None,
        missing=[],
    )


# --- queries ------------------------------------------------------------------


async def current_outcome(engine: AsyncEngine, case_id: uuid.UUID) -> dict | None:
    """The head revision of a case (derived view)."""
    async with engine.begin() as conn:
        head = (
            await conn.execute(
                select(outcome_revisions)
                .where(outcome_revisions.c.case_id == case_id)
                .order_by(outcome_revisions.c.revision.desc())
                .limit(1)
            )
        ).mappings().first()
    if head is None:
        return None
    return {
        "outcome_id": str(head.id),
        "case_id": str(head.case_id),
        "revision": head.revision,
        "supersedes_outcome_id": (
            None if head.supersedes_outcome_id is None else str(head.supersedes_outcome_id)
        ),
        "status": head.status,
        "recorded_at": head.recorded_at.isoformat(),
        "asset_return": None if head.asset_return is None else decimal_str(head.asset_return),
        "benchmark_return": (
            None if head.benchmark_return is None else decimal_str(head.benchmark_return)
        ),
        "excess_return": None if head.excess_return is None else decimal_str(head.excess_return),
        "prices_and_actions_snapshot_id": (
            None
            if head.prices_and_actions_snapshot_id is None
            else str(head.prices_and_actions_snapshot_id)
        ),
        "resolver_version": head.resolver_version,
        "correction_reason": head.correction_reason,
        "basis": head.basis,
    }


async def verify_outcome_chains(engine: AsyncEngine) -> dict:
    """Structural check of every case's revision chain: numbering is
    contiguous from 1 and each revision supersedes exactly the
    previous one (no forks, no orphans)."""
    issues: list[str] = []
    async with engine.begin() as conn:
        case_ids = (
            await conn.execute(
                select(outcome_revisions.c.case_id).distinct()
            )
        ).scalars().all()
        for case_id in case_ids:
            revisions = (
                await conn.execute(
                    select(outcome_revisions)
                    .where(outcome_revisions.c.case_id == case_id)
                    .order_by(outcome_revisions.c.revision)
                )
            ).mappings().all()
            prev = None
            for i, rev in enumerate(revisions):
                if rev.revision != i + 1:
                    issues.append(
                        f"case {case_id}: revision numbering breaks at "
                        f"{rev.revision} (position {i})"
                    )
                expected_parent = None if prev is None else prev.id
                if rev.supersedes_outcome_id != expected_parent:
                    issues.append(
                        f"case {case_id} revision {rev.revision}: supersedes "
                        f"{rev.supersedes_outcome_id} instead of head "
                        f"{expected_parent}"
                    )
                prev = rev
    return {"ok": not issues, "issues": issues, "cases_checked": len(case_ids)}
