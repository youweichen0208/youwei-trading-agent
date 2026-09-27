"""S02a acceptance scenarios: budget reservation, settlement,
reconciliation.

Rules under test (per the S02 decision):
- every call/retry reserves BEFORE the upstream request; concurrent
  reservations cannot exceed the run total
- reserve and settle are idempotent per call key (duplicate delivery
  of the same settlement must not double-book)
- unknown cost after timeout/cancel stays reserved and shows up in the
  pending-reconciliation list
- a fenced (late) attempt's real costs are still booked: business
  result rejection never suppresses cost recording
"""

import asyncio
import uuid

from youwei_core.budget.service import (
    BudgetExceeded,
    pending_reconciliation,
    release,
    reserve,
    settle,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, get_run_view, submit_run


async def _make_run(engine, tenant_id, total_micros):
    submission = RunSubmission(
        kind="research",
        total_budget_micros=total_micros,
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    result = await submit_run(engine, tenant_id, submission, f"b-{uuid.uuid4().hex[:8]}")
    return result.run_id


async def test_concurrent_reservations_cannot_exceed_total(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)

    async def try_reserve(call_key):
        try:
            await reserve(
                db_engine, run_id,
                attempt_id=uuid.uuid4(), call_key=call_key, amount_micros=700_000,
            )
            return "reserved"
        except BudgetExceeded:
            return "exceeded"

    outcomes = await asyncio.gather(try_reserve("call-1"), try_reserve("call-2"))
    assert sorted(outcomes) == ["exceeded", "reserved"]

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 700_000
    assert view["settled_micros"] == 0


async def test_reserve_is_idempotent_per_call_key(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    attempt = uuid.uuid4()

    r1 = await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)
    r2 = await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)
    assert r1.status == "reserved"
    assert r2.status == "duplicate"
    assert r2.amount_micros == 500_000

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 500_000  # not doubled


async def test_settle_releases_reservation_and_books_actual(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    attempt = uuid.uuid4()

    await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)
    status = await settle(
        db_engine, run_id, attempt_id=attempt, call_key="c1", actual_micros=420_000
    )
    assert status == "settled"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 0
    assert view["settled_micros"] == 420_000


async def test_settle_is_idempotent_duplicate_delivery(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    attempt = uuid.uuid4()

    await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)
    s1 = await settle(db_engine, run_id, attempt_id=attempt, call_key="c1", actual_micros=420_000)
    s2 = await settle(db_engine, run_id, attempt_id=attempt, call_key="c1", actual_micros=420_000)
    assert s1 == "settled"
    assert s2 == "duplicate"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["settled_micros"] == 420_000  # not 840_000
    assert view["reserved_micros"] == 0


async def test_unknown_cost_stays_reserved_pending_reconciliation(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    attempt = uuid.uuid4()

    # call sent, cost never learned (timeout/cancel): reserve stays open
    await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)

    pending = await pending_reconciliation(db_engine, run_id)
    assert [p.call_key for p in pending] == ["c1"]
    assert pending[0].amount_micros == 500_000

    # budget is blocked until reconciled: only 500k headroom remains
    try:
        await reserve(
            db_engine, run_id,
            attempt_id=uuid.uuid4(), call_key="c2", amount_micros=600_000,
        )
        raise AssertionError("should have exceeded")
    except BudgetExceeded:
        pass

    # reconciliation books the actual cost learned later
    status = await settle(
        db_engine, run_id, attempt_id=attempt, call_key="c1", actual_micros=450_000
    )
    assert status == "settled"
    assert await pending_reconciliation(db_engine, run_id) == []

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 0
    assert view["settled_micros"] == 450_000


async def test_release_returns_reservation_on_cancel(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    attempt = uuid.uuid4()

    await reserve(db_engine, run_id, attempt_id=attempt, call_key="c1", amount_micros=500_000)
    status = await release(db_engine, run_id, call_key="c1")
    assert status == "released"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 0
    assert view["settled_micros"] == 0
    assert await pending_reconciliation(db_engine, run_id) == []


async def test_late_attempt_costs_still_booked(db_engine, tenant_id):
    """A fenced attempt's business result is rejected, but its real
    upstream cost is still recorded against the run."""
    from youwei_core.jobs.worker import claim_next_job, complete_attempt

    run_id = await _make_run(db_engine, tenant_id, 1_000_000)
    claimed = await claim_next_job(db_engine, "w1", lease_ttl=0.1)
    await asyncio.sleep(0.4)  # lease expires; attempt will be fenced

    # cost of the (now stale) attempt: reserve + settle both still work
    r = await reserve(
        db_engine, run_id,
        attempt_id=claimed.attempt_id, call_key=f"{claimed.attempt_id}:1",
        amount_micros=100_000,
    )
    assert r.status == "reserved"

    outcome = await complete_attempt(db_engine, claimed.job_id, 1, {"late": True})
    assert outcome == "fenced"  # business result rejected...

    s = await settle(
        db_engine, run_id,
        attempt_id=claimed.attempt_id, call_key=f"{claimed.attempt_id}:1",
        actual_micros=90_000,
    )
    assert s == "settled"  # ...but the real cost is booked

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["settled_micros"] == 90_000
