"""S02b: LLM gateway client with budget integration.

The Core-side contract for every model call (architecture sections
2/10/11 and the S02 budget rules):
- reserve BEFORE the upstream request (conservative max estimate)
- settle AFTER the response is priced (actual from usage)
- transport/gateway errors without usage -> release (no bookable cost)
- timeout/cancel with unknown cost -> reservation stays, pending
  reconciliation
- fail-closed: when the budget ledger is unreachable, no request is
  sent at all
"""

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest

from youwei_core.budget.service import BudgetExceeded, pending_reconciliation
from youwei_core.jobs.service import JobSubmission, RunSubmission, get_run_view, submit_run
from youwei_core.llm.client import GatewayClient, GatewayError
from youwei_core.llm.pricing import MODEL_PRICES, actual_cost, estimate_max_cost

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def _ok_response(request):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-x",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": USAGE,
            "model": "glm-5.3",
        },
    )


async def _make_run(engine, tenant_id, total=1_000_000):
    submission = RunSubmission(
        kind="research",
        total_budget_micros=total,
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    result = await submit_run(engine, tenant_id, submission, f"llm-{uuid.uuid4().hex[:8]}")
    return result.run_id


def _client(handler) -> tuple[GatewayClient, list]:
    calls = []

    def transport_handler(request):
        calls.append(request)
        return handler(request)

    return (
        GatewayClient(
            "http://gateway.test",
            api_key="sk-gw",
            transport=httpx.MockTransport(transport_handler),
        ),
        calls,
    )


async def test_placeholder_rate_books_estimate_not_confirmed(db_engine, tenant_id):
    """A placeholder rate card prices usage as an *estimate*: it books to
    estimated_micros, never settled_micros (which stays for reconciled actuals)."""
    run_id = await _make_run(db_engine, tenant_id)
    client, calls = _client(_ok_response)

    result = await client.chat(
        db_engine,
        run_id=run_id,
        attempt_id=uuid.uuid4(),
        call_seq=1,
        model="glm-5.3",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1000,
        approx_input_tokens=500,
    )
    assert result.content == "OK"

    # exactly one upstream call, authorized with the gateway key
    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == "Bearer sk-gw"

    # glm-5.3 is a placeholder rate -> estimated, reservation released.
    view = await get_run_view(db_engine, tenant_id, run_id)
    expected = actual_cost("glm-5.3", USAGE)
    assert view["settled_micros"] == 0
    assert view["estimated_micros"] == expected
    assert view["reserved_micros"] == 0
    assert await pending_reconciliation(db_engine, run_id) == []


async def test_reconciled_rate_books_confirmed_settled(db_engine, tenant_id):
    """A reconciled rate card prices usage as a confirmed actual, booked to
    settled_micros."""
    from youwei_core.llm.pricing import (
        CostMap, RateCard, RATE_STATUS_RECONCILED,
    )
    reconciled = CostMap(
        version="test-reconciled-v1", currency="USD",
        models={
            "glm-5.3": RateCard(
                input=Decimal("10"),
                output=Decimal("40"),
                status=RATE_STATUS_RECONCILED,
            ),
        },
    )
    run_id = await _make_run(db_engine, tenant_id)
    # inject the reconciled cost map via a custom client
    calls = []

    def transport_handler(request):
        calls.append(request)
        return _ok_response(request)

    client = GatewayClient(
        "http://gateway.test", api_key="sk-gw",
        transport=httpx.MockTransport(transport_handler),
        cost_map=reconciled,
    )
    await client.chat(
        db_engine, run_id=run_id, attempt_id=uuid.uuid4(), call_seq=1,
        model="glm-5.3", messages=[{"role": "user", "content": "hi"}],
        max_tokens=1000, approx_input_tokens=500,
    )
    view = await get_run_view(db_engine, tenant_id, run_id)
    expected = actual_cost("glm-5.3", USAGE)
    assert view["settled_micros"] == expected
    assert view["estimated_micros"] == 0
    assert view["reserved_micros"] == 0


async def test_budget_exceeded_sends_nothing_upstream(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, total=100)  # tiny budget
    client, calls = _client(_ok_response)

    with pytest.raises(BudgetExceeded):
        await client.chat(
            db_engine,
            run_id=run_id,
            attempt_id=uuid.uuid4(),
            call_seq=1,
            model="glm-5.3",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1000,
            approx_input_tokens=500,
        )
    assert calls == []  # fail-closed: rejected before any request


async def test_ledger_unreachable_fails_closed(db_engine, tenant_id):
    """Counter failure must refuse the request, never bypass budget."""
    run_id = await _make_run(db_engine, tenant_id)
    client, calls = _client(_ok_response)

    from youwei_core.db.engine import make_engine

    dead_engine = make_engine(
        "postgresql+asyncpg://youwei:youwei@127.0.0.1:1/youwei", pool_size=1
    )
    try:
        with pytest.raises(Exception):  # connection error, not GatewayError
            await client.chat(
                dead_engine,
                run_id=run_id,
                attempt_id=uuid.uuid4(),
                call_seq=1,
                model="glm-5.3",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1000,
                approx_input_tokens=500,
            )
    finally:
        await dead_engine.dispose()
    assert calls == []  # nothing sent when the ledger is down


async def test_transport_error_releases_reservation(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)

    def broken(request):
        raise httpx.ConnectError("connection refused")

    client, calls = _client(broken)

    with pytest.raises(GatewayError):
        await client.chat(
            db_engine,
            run_id=run_id,
            attempt_id=uuid.uuid4(),
            call_seq=1,
            model="glm-5.3",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1000,
            approx_input_tokens=500,
        )
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 0
    assert view["settled_micros"] == 0


async def test_gateway_error_response_releases(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)

    def err500(request):
        return httpx.Response(500, json={"error": "upstream boom"})

    client, _ = _client(err500)

    with pytest.raises(GatewayError):
        await client.chat(
            db_engine,
            run_id=run_id,
            attempt_id=uuid.uuid4(),
            call_seq=1,
            model="glm-5.3",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1000,
            approx_input_tokens=500,
        )
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == 0


async def test_timeout_keeps_reservation_pending_reconciliation(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id)

    def slow(request):
        raise httpx.ReadTimeout("upstream too slow")

    client, _ = _client(slow)

    with pytest.raises(httpx.ReadTimeout):
        await client.chat(
            db_engine,
            run_id=run_id,
            attempt_id=uuid.uuid4(),
            call_seq=1,
            model="glm-5.3",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1000,
            approx_input_tokens=500,
        )

    # unknown cost: reservation stays open and blocks the budget
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["reserved_micros"] == estimate_max_cost(
        "glm-5.3", max_tokens=1000, approx_input_tokens=500
    )
    pending = await pending_reconciliation(db_engine, run_id)
    assert len(pending) == 1


def test_estimate_is_conservative_max():
    est = estimate_max_cost("glm-5.3", max_tokens=1000, approx_input_tokens=500)
    prices = MODEL_PRICES["glm-5.3"]
    assert est == 500 * prices["input"] + 1000 * prices["output"]
