"""S07k: versioned cost map — billing categories, currency, precision,
rounding, and placeholder-vs-reconciled status.

Pure logic, no PostgreSQL. Pins the contract that a placeholder rate card
prices usage as an *estimate* (never a confirmed actual) and that the five
billing buckets are priced independently and never summed past their
overlap.
"""

from decimal import Decimal

import pytest

from youwei_core.llm.pricing import (
    BILLING_CATEGORIES,
    COST_CONFIRMED,
    COST_ESTIMATED,
    RATE_STATUS_PLACEHOLDER,
    RATE_STATUS_RECONCILED,
    CostMap,
    RateCard,
    UnknownModel,
    estimate_max_cost_for,
    price_usage,
)


def _card(input=Decimal("10"), output=Decimal("40"), *, cache_read=Decimal("0"),
          cache_write=Decimal("0"), reasoning=Decimal("0"),
          status=RATE_STATUS_PLACEHOLDER) -> RateCard:
    return RateCard(
        input=input, output=output, cache_read=cache_read,
        cache_write=cache_write, reasoning=reasoning, status=status,
    )


def _map(model="m", card=None) -> CostMap:
    return CostMap(
        version="test-v1", currency="USD",
        models={model: card or _card()},
    )


def test_five_categories_are_priced_independently():
    card = _card(
        input=Decimal("10"), output=Decimal("40"),
        cache_read=Decimal("1"), cache_write=Decimal("5"), reasoning=Decimal("2"),
        status=RATE_STATUS_RECONCILED,
    )
    cm = _map(card=card)
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 30,
        "cache_write_tokens": 10,
        "reasoning_tokens": 20,
    }
    result = price_usage(cm, "m", usage)
    # 100*10 + 50*40 + 30*1 + 10*5 + 20*2 = 1000 + 2000 + 30 + 50 + 40
    assert result.amount_micros == 3120
    assert result.status == COST_CONFIRMED


def test_prompt_and_completion_are_not_summed_on_top_of_buckets():
    """prompt_tokens (input+cache) and completion_tokens (output) overlap the
    buckets; a usage block that reports both must not double-count."""
    card = _card(input=Decimal("10"), output=Decimal("40"), status=RATE_STATUS_RECONCILED)
    cm = _map(card=card)
    # OpenAI-shaped block reports prompt/completion PLUS the canonical buckets.
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    result = price_usage(cm, "m", usage)
    # Only input/output buckets are priced: 100*10 + 50*40 = 3000.
    assert result.amount_micros == 3000


def test_placeholder_card_is_estimated_not_confirmed():
    cm = _map(card=_card(status=RATE_STATUS_PLACEHOLDER))
    result = price_usage(cm, "m", {"input_tokens": 100, "output_tokens": 50})
    assert result.status == COST_ESTIMATED
    assert result.amount_micros == 3000  # still computed, but as an estimate


def test_reconciled_card_is_confirmed():
    cm = _map(card=_card(status=RATE_STATUS_RECONCILED))
    result = price_usage(cm, "m", {"input_tokens": 100, "output_tokens": 50})
    assert result.status == COST_CONFIRMED


def test_non_integer_rate_is_exact_and_rounds_half_up():
    # $0.5 per 1M tokens == 0.5 micros/token: 3 tokens -> 1.5 micros -> 2.
    card = _card(input=Decimal("0.5"), output=Decimal("0.5"),
                 status=RATE_STATUS_RECONCILED)
    cm = _map(card=card)
    result = price_usage(cm, "m", {"input_tokens": 3, "output_tokens": 0})
    assert result.amount_micros == 2  # 1.5 rounds half-up to 2


def test_half_up_rounding_on_exact_half_micro():
    # 0.5 micros rounds half-up to 1 (Decimal ROUND_HALF_UP, not banker's).
    half_card = _card(input=Decimal("0.5"), output=Decimal("0"),
                      status=RATE_STATUS_RECONCILED)
    half_cm = _map(card=half_card)
    result = price_usage(half_cm, "m", {"input_tokens": 1})
    assert result.amount_micros == 1  # 0.5 -> half-up to 1


def test_estimate_max_cost_uses_ceil():
    card = _card(input=Decimal("0.5"), output=Decimal("0.5"),
                 status=RATE_STATUS_PLACEHOLDER)
    cm = _map(card=card)
    # 3 input + 3 output: 1.5 + 1.5 = 3.0 exactly -> 3.
    assert estimate_max_cost_for(cm, "m", max_tokens=3, approx_input_tokens=3) == 3


def test_estimate_max_cost_covers_fractional_worst_case():
    card = _card(input=Decimal("0.5"), output=Decimal("0.5"),
                 status=RATE_STATUS_PLACEHOLDER)
    cm = _map(card=card)
    # 1 input + 1 output = 0.5 + 0.5 = 1.0 -> 1 (no fraction).
    # 1 input + 2 output = 0.5 + 1.0 = 1.5 -> ceil 2.
    assert estimate_max_cost_for(cm, "m", max_tokens=2, approx_input_tokens=1) == 2


def test_unknown_model_raises():
    cm = _map(model="m")
    with pytest.raises(UnknownModel):
        price_usage(cm, "missing", {"input_tokens": 1})


def test_unknown_billing_category_rejected():
    card = _card()
    with pytest.raises(ValueError):
        card.rate("nonexistent")


def test_missing_usage_tokens_are_zero():
    card = _card(input=Decimal("10"), output=Decimal("40"),
                 status=RATE_STATUS_RECONCILED)
    cm = _map(card=card)
    result = price_usage(cm, "m", {})
    assert result.amount_micros == 0
    assert result.status == COST_CONFIRMED


def test_billing_categories_match_hermes_buckets():
    assert BILLING_CATEGORIES == ("input", "output", "cache_read", "cache_write", "reasoning")
