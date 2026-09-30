"""Budget ledger: per-call reservation before upstream requests,
idempotent settlement, release on cancel, and reconciliation of
unknown costs.

Invariants:
- runs.reserved_micros never exceeds runs.total_budget_micros
  (enforced by the conditional UPDATE in reserve())
- every entry is append-only and uniquely keyed per (run, idem_key):
  duplicate delivery of a reserve or settle is a no-op that returns
  the original outcome
- an unmatched reserve after its attempt ended blocks that budget
  until reconciliation settles or releases it (conservative)
- costs are booked regardless of the attempt's business outcome:
  fencing rejects business writes, never cost writes
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import budget_entries, events, runs


class BudgetExceeded(Exception):
    pass


class RunMissing(Exception):
    pass


@dataclass
class ReserveResult:
    status: str  # "reserved" | "duplicate"
    amount_micros: int


@dataclass
class PendingCall:
    call_key: str
    amount_micros: int
    entry_id: uuid.UUID


def _reserve_key(call_key: str) -> str:
    return f"{call_key}:reserve"


def _settle_key(call_key: str) -> str:
    return f"{call_key}:settle"


def _estimate_key(call_key: str) -> str:
    return f"{call_key}:estimated"


def _release_key(call_key: str) -> str:
    return f"{call_key}:release"


async def reserve(
    engine: AsyncEngine,
    run_id: uuid.UUID,
    *,
    attempt_id: uuid.UUID,
    call_key: str,
    amount_micros: int,
) -> ReserveResult:
    """Reserve budget before an upstream call. Raises BudgetExceeded
    when the reservation would push the run past its total."""
    if amount_micros < 0:
        raise ValueError("amount_micros must be >= 0")

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == _reserve_key(call_key),
                )
            )
        ).mappings().first()
        if existing is not None:
            return ReserveResult(status="duplicate", amount_micros=existing.amount_micros)

        updated = await conn.execute(
            update(runs)
            .where(
                runs.c.id == run_id,
                runs.c.reserved_micros + amount_micros <= runs.c.total_budget_micros,
            )
            .values(
                reserved_micros=runs.c.reserved_micros + amount_micros,
                updated_at=func.now(),
            )
        )
        if updated.rowcount == 0:
            exists = (
                await conn.execute(select(func.count()).where(runs.c.id == run_id))
            ).scalar_one()
            if not exists:
                raise RunMissing(str(run_id))
            raise BudgetExceeded(
                f"run {run_id}: reservation {amount_micros} would exceed total"
            )

        await conn.execute(
            budget_entries.insert().values(
                id=uuid.uuid4(),
                run_id=run_id,
                attempt_id=attempt_id,
                entry_type="reserve",
                amount_micros=amount_micros,
                idem_key=_reserve_key(call_key),
            )
        )
        return ReserveResult(status="reserved", amount_micros=amount_micros)


async def settle(
    engine: AsyncEngine,
    run_id: uuid.UUID,
    *,
    attempt_id: uuid.UUID,
    call_key: str,
    actual_micros: int,
    confirmed: bool = True,
) -> str:
    """Book the cost of a call and release its reservation.

    ``confirmed=True`` books to ``settled_micros`` (a reconciled actual).
    ``confirmed=False`` books to ``estimated_micros`` (a placeholder-rate
    estimate) and must NEVER be treated as a confirmed actual; it awaits
    reconciliation via a later ``adjust``. Both release the reservation.

    Idempotent per (call_key, confirmed): a duplicate settle returns
    "duplicate" without double-booking. The two flags use distinct idem
    keys so an estimate does not collide with a later confirmed settle.
    """
    if actual_micros < 0:
        raise ValueError("actual_micros must be >= 0")

    idem_key = _settle_key(call_key) if confirmed else _estimate_key(call_key)
    entry_type = "settle" if confirmed else "settle_estimate"

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == idem_key,
                )
            )
        ).mappings().first()
        if existing is not None:
            return "duplicate"

        reserve_entry = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == _reserve_key(call_key),
                )
            )
        ).mappings().first()
        reserved_amount = reserve_entry.amount_micros if reserve_entry else 0

        # Release the reservation and book the cost to the confirmed or
        # estimated bucket. The two are never summed into one column.
        values = {
            "reserved_micros": func.greatest(
                runs.c.reserved_micros - reserved_amount, 0
            ),
            "updated_at": func.now(),
        }
        if confirmed:
            values["settled_micros"] = runs.c.settled_micros + actual_micros
        else:
            values["estimated_micros"] = runs.c.estimated_micros + actual_micros
        await conn.execute(update(runs).where(runs.c.id == run_id).values(**values))

        await conn.execute(
            budget_entries.insert().values(
                id=uuid.uuid4(),
                run_id=run_id,
                attempt_id=attempt_id,
                entry_type=entry_type,
                amount_micros=actual_micros,
                idem_key=idem_key,
            )
        )
        await conn.execute(
            events.insert().values(
                tenant_id=(
                    select(runs.c.tenant_id).where(runs.c.id == run_id).scalar_subquery()
                ),
                run_id=run_id,
                event_type="budget.settled",
                payload={
                    "call_key": call_key,
                    "amount_micros": actual_micros,
                    "confirmed": confirmed,
                },
            )
        )
        return "settled"


async def release(engine: AsyncEngine, run_id: uuid.UUID, *, call_key: str) -> str:
    """Return a reservation without a call result (cancel path).
    No-op when the call was already settled."""
    async with engine.begin() as conn:
        settled = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == _settle_key(call_key),
                )
            )
        ).mappings().first()
        if settled is not None:
            return "already_settled"

        existing = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == _release_key(call_key),
                )
            )
        ).mappings().first()
        if existing is not None:
            return "duplicate"

        reserve_entry = (
            await conn.execute(
                select(budget_entries).where(
                    budget_entries.c.run_id == run_id,
                    budget_entries.c.idem_key == _reserve_key(call_key),
                )
            )
        ).mappings().first()
        if reserve_entry is None:
            return "no_reservation"

        await conn.execute(
            update(runs)
            .where(runs.c.id == run_id)
            .values(
                reserved_micros=func.greatest(
                    runs.c.reserved_micros - reserve_entry.amount_micros, 0
                ),
                updated_at=func.now(),
            )
        )
        await conn.execute(
            budget_entries.insert().values(
                id=uuid.uuid4(),
                run_id=run_id,
                attempt_id=reserve_entry.attempt_id,
                entry_type="release",
                amount_micros=reserve_entry.amount_micros,
                idem_key=_release_key(call_key),
            )
        )
        return "released"


async def pending_reconciliation(
    engine: AsyncEngine,
    run_id: uuid.UUID,
) -> list[PendingCall]:
    """Reserves with no matching settle or release: the conservative
    pending list after timeout/cancel with unknown cost. Their budget
    stays blocked until reconcile (a late settle) or release."""
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    select(budget_entries)
                    .where(
                        budget_entries.c.run_id == run_id,
                        budget_entries.c.entry_type == "reserve",
                        ~budget_entries.c.idem_key.in_(
                            select(
                                func.replace(
                                    budget_entries.c.idem_key, ":settle", ":reserve"
                                )
                            ).where(
                                budget_entries.c.run_id == run_id,
                                budget_entries.c.entry_type == "settle",
                            )
                        ),
                        # A placeholder-rate estimate also closes the reserve
                        # (the cost is booked, just not confirmed).
                        ~budget_entries.c.idem_key.in_(
                            select(
                                func.replace(
                                    budget_entries.c.idem_key, ":estimated", ":reserve"
                                )
                            ).where(
                                budget_entries.c.run_id == run_id,
                                budget_entries.c.entry_type == "settle_estimate",
                            )
                        ),
                        ~budget_entries.c.idem_key.in_(
                            select(
                                func.replace(
                                    budget_entries.c.idem_key, ":release", ":reserve"
                                )
                            ).where(
                                budget_entries.c.run_id == run_id,
                                budget_entries.c.entry_type == "release",
                            )
                        ),
                    )
                    .order_by(budget_entries.c.created_at)
                )
            )
            .mappings()
            .all()
        )
    return [
        PendingCall(
            call_key=row.idem_key[: -len(":reserve")],
            amount_micros=row.amount_micros,
            entry_id=row.id,
        )
        for row in rows
    ]
