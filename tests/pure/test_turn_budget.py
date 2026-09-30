"""S07k: turn-level budget settlement decision (pure logic).

Pins the rule that only a complete turn-level usage report is priced and
settled, and that a placeholder rate settles as an estimate (never
confirmed). The actual reserve/settle SQL is DB-level; here the settle
call is a spy so the decision logic is testable without PostgreSQL.
"""

import uuid
from decimal import Decimal

import pytest

from youwei_core.ledger.pipeline import TurnBudgetWiring, _settle_turn_budget
from youwei_core.llm.pricing import (
    RATE_STATUS_PLACEHOLDER,
    RATE_STATUS_RECONCILED,
    CostMap,
    RateCard,
)


def _budget(cost_map):
    return TurnBudgetWiring(
        engine=object(),  # not used; settle is a spy
        attempt_id=uuid.uuid4(),
        turn_reserve_micros=1_000_000,
        cost_map=cost_map,
    )


def _complete_usage(**overrides):
    base = {
        "source": "session_delta",
        "scope": "chat_turn",
        "complete": True,
        "incomplete_reasons": [],
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "api_calls": 1,
    }
    base.update(overrides)
    return base


async def _run(cost_map, usage, monkeypatch):
    calls = []

    async def spy_settle(engine, run_id, *, attempt_id, call_key, actual_micros, confirmed):
        calls.append({"actual_micros": actual_micros, "confirmed": confirmed})

    monkeypatch.setattr("youwei_core.ledger.pipeline.settle", spy_settle)
    budget = _budget(cost_map)
    await _settle_turn_budget(budget, uuid.uuid4(), "call-key", "m", usage)
    return calls


async def test_complete_placeholder_settles_as_estimate(monkeypatch):
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_PLACEHOLDER)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(cm, _complete_usage(), monkeypatch)
    assert len(calls) == 1
    # 100*10 + 50*40 = 3000 micros, placeholder -> confirmed=False
    assert calls[0] == {"actual_micros": 3000, "confirmed": False}


async def test_complete_reconciled_settles_as_confirmed(monkeypatch):
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_RECONCILED)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(cm, _complete_usage(), monkeypatch)
    assert len(calls) == 1
    assert calls[0] == {"actual_micros": 3000, "confirmed": True}


async def test_incomplete_usage_leaves_reservation_open(monkeypatch):
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_RECONCILED)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(
        cm, _complete_usage(complete=False, incomplete_reasons=["turn_failed"]),
        monkeypatch,
    )
    assert calls == []  # unknown cost: never settled as zero


async def test_unavailable_usage_leaves_reservation_open(monkeypatch):
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_RECONCILED)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(
        cm, {"source": "unavailable", "scope": "unknown", "complete": False},
        monkeypatch,
    )
    assert calls == []


async def test_non_dict_usage_leaves_reservation_open(monkeypatch):
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_RECONCILED)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(cm, None, monkeypatch)
    assert calls == []


async def test_last_call_fallback_is_not_settled(monkeypatch):
    """A last-call fallback (scope=last_api_call, complete=False) must not be
    priced as the turn total — it only covers the final API call."""
    card = RateCard(input=Decimal("10"), output=Decimal("40"),
                    status=RATE_STATUS_RECONCILED)
    cm = CostMap(version="v1", models={"m": card})
    calls = await _run(
        cm, {
            "source": "last_call_fallback", "scope": "last_api_call",
            "complete": False, "incomplete_reasons": ["last_call_scope_only"],
            "input_tokens": 10, "output_tokens": 5,
        },
        monkeypatch,
    )
    assert calls == []
