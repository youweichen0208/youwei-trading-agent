"""Versioned cost map for the LLM gateway path.

The cost map is the single source of truth for converting token usage into
micro-USD amounts. It is versioned and every rate card carries a
``status`` that says whether the rates are placeholders (not yet reconciled
against the Volcengine console) or reconciled actuals.

Design invariants (S07k, budget settlement slice):

- Currency is USD; amounts are integer *micros* (1e-6 USD).
- Rates are ``Decimal`` micros-per-token so non-integer list prices (e.g.
  $0.5 per 1M tokens = 0.5 micros/token) are exact.
- Billing categories are the five independent buckets of the gateway usage
  (``input``, ``output``, ``cache_read``, ``cache_write``, ``reasoning``).
  They do NOT overlap for pricing: ``prompt_tokens`` and
  ``completion_tokens`` are *derived* (prompt = input + cache_read +
  cache_write; completion = output) and must never be summed into cost on
  top of the buckets.
- A placeholder rate card prices usage as an *estimate* — it is NEVER
  recorded as a confirmed actual. A reconciled card prices as *confirmed*.
- Rounding: each category is multiplied exactly (Decimal), the five
  categories are summed, and the total is rounded half-up to an integer
  micro amount. Reservation estimates use ceil so a reserve always covers
  the worst case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Mapping

# The five independent billing buckets (mirror Hermes CanonicalUsage).
BILLING_CATEGORIES = (
    "input",
    "output",
    "cache_read",
    "cache_write",
    "reasoning",
)

# Rate-card status. A placeholder is a configured stand-in that has NOT
# been reconciled against provider billing; it may price usage for an
# *estimate* but never a *confirmed* actual.
RATE_STATUS_PLACEHOLDER = "placeholder"
RATE_STATUS_RECONCILED = "reconciled"

# Cost result status: whether the computed amount is a confirmed actual,
# an estimate from placeholder/unreconciled rates, or unknown (no rates).
COST_CONFIRMED = "confirmed"
COST_ESTIMATED = "estimated"
COST_UNKNOWN = "unknown"


class UnknownModel(Exception):
    pass


@dataclass(frozen=True)
class RateCard:
    """One model's per-category rates (Decimal micros per token)."""

    input: Decimal
    output: Decimal
    cache_read: Decimal = Decimal("0")
    cache_write: Decimal = Decimal("0")
    reasoning: Decimal = Decimal("0")
    status: str = RATE_STATUS_PLACEHOLDER

    def rate(self, category: str) -> Decimal:
        try:
            return getattr(self, category)
        except AttributeError:
            raise ValueError(f"unknown billing category {category!r}") from None


@dataclass(frozen=True)
class CostMap:
    """A versioned, currency-fixed collection of rate cards."""

    version: str
    currency: str = "USD"
    models: Mapping[str, RateCard] = field(default_factory=dict)

    def rate_card(self, model: str) -> RateCard:
        try:
            return self.models[model]
        except KeyError:
            raise UnknownModel(
                f"no rate card configured for model {model!r} "
                f"(cost-map {self.version!r})"
            ) from None


@dataclass(frozen=True)
class CostBreakdown:
    """Per-category token counts and their priced amounts (Decimal micros)."""

    tokens: Mapping[str, int]
    amounts: Mapping[str, Decimal]


@dataclass(frozen=True)
class CostResult:
    """A priced usage block: the integer micro amount plus its status.

    ``status`` is ``confirmed`` only when every rate card used is reconciled
    and every billed category is priced; otherwise ``estimated`` (placeholder
    rates) or ``unknown`` (no usable rates / unknown usage). A caller MUST
    never record an ``estimated`` or ``unknown`` result as a confirmed
    actual_micros.
    """

    amount_micros: int
    status: str
    breakdown: CostBreakdown


def _usage_tokens(usage: Mapping[str, int]) -> Mapping[str, int]:
    """Extract the five billing buckets from an OpenAI-shaped usage block,
    tolerating provider naming variants. Missing/unknown buckets are 0 (only
    a *confirmed* zero when the provider actually reported zero — the caller
    cannot distinguish a missing key from a reported zero here, so a status
    of confirmed requires the caller to know the usage was complete)."""
    def _g(*names: str) -> int:
        for name in names:
            v = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
            if v is None:
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                return n
        return 0

    return {
        "input": _g("input_tokens", "prompt_tokens"),
        "output": _g("output_tokens", "completion_tokens"),
        "cache_read": _g("cache_read_tokens", "cache_read_input_tokens"),
        "cache_write": _g("cache_write_tokens", "cache_creation_input_tokens"),
        "reasoning": _g("reasoning_tokens"),
    }


def _round_micros(value: Decimal, *, ceiling: bool = False) -> int:
    """Round a Decimal micro amount to an integer micro amount.

    ``ceiling=True`` rounds up (for conservative reservations); otherwise
    round half-up (for settled actuals)."""
    rounding = ROUND_CEILING if ceiling else ROUND_HALF_UP
    return int(value.quantize(Decimal("1"), rounding=rounding))


def price_usage(cost_map: CostMap, model: str, usage: Mapping[str, int]) -> CostResult:
    """Price one usage block against the cost map.

    Returns the integer micro amount plus a status. ``confirmed`` requires
    the rate card to be reconciled; a placeholder card yields ``estimated``.
    """
    card = cost_map.rate_card(model)
    tokens = _usage_tokens(usage)
    amounts: dict[str, Decimal] = {}
    for category in BILLING_CATEGORIES:
        amounts[category] = Decimal(tokens[category]) * card.rate(category)

    total = sum(amounts.values(), Decimal("0"))
    status = COST_CONFIRMED if card.status == RATE_STATUS_RECONCILED else COST_ESTIMATED
    return CostResult(
        amount_micros=_round_micros(total),
        status=status,
        breakdown=CostBreakdown(tokens=tokens, amounts=amounts),
    )


def estimate_max_cost_for(
    cost_map: CostMap,
    model: str,
    *,
    max_tokens: int,
    approx_input_tokens: int,
) -> int:
    """Conservative maximum cost for one call against a cost map: the full
    output budget plus the approximate input, each priced at the model's list
    rate, rounded up.

    The reservation must always cover the worst case, so this uses ceil and
    prices the full ``max_tokens`` as output (plus the approximate input).
    """
    card = cost_map.rate_card(model)
    # Worst case: all output tokens billed at output rate, all input at input
    # rate (cache/reasoning are subsets of those budgets and not added on top).
    total = (
        Decimal(approx_input_tokens) * card.input
        + Decimal(max_tokens) * card.output
    )
    return _round_micros(total, ceiling=True)


# --- legacy shims (S02b) ----------------------------------------------------
#
# Kept for source compatibility while the gateway client migrates to the
# versioned CostMap. These operate on the default placeholder map and must
# NOT be used to book confirmed actuals (their rates are placeholders).


# 占位价（micros/token）：正式运行前须与火山控制台计费对账后替换。
_MODEL_PRICES_LEGACY: Mapping[str, Mapping[str, int]] = {
    "glm-5.3": {"input": 10, "output": 40},
    "glm-5.3-flash": {"input": 2, "output": 8},
    "deepseek-v4-pro": {"input": 8, "output": 32},
    "deepseek-v4-flash": {"input": 1, "output": 4},
    "qwen3.8-flash": {"input": 1, "output": 4},
}

# The default placeholder cost map built from the legacy stand-in prices.
DEFAULT_COST_MAP = CostMap(
    version="cost-map-v1-placeholder",
    currency="USD",
    models={
        model: RateCard(
            input=Decimal(str(p["input"])),
            output=Decimal(str(p["output"])),
            status=RATE_STATUS_PLACEHOLDER,
        )
        for model, p in _MODEL_PRICES_LEGACY.items()
    },
)

# Legacy alias: the raw placeholder table (micros/token).
MODEL_PRICES: Mapping[str, Mapping[str, int]] = _MODEL_PRICES_LEGACY


def actual_cost(model: str, usage: Mapping[str, int]) -> int:
    """Legacy shim: price usage at the placeholder rates (estimated, NOT a
    confirmed actual). New code must use ``price_usage`` and check status."""
    result = price_usage(DEFAULT_COST_MAP, model, usage)
    return result.amount_micros


def estimate_max_cost(model: str, *, max_tokens: int, approx_input_tokens: int) -> int:
    """Legacy shim: conservative max estimate at placeholder rates."""
    return estimate_max_cost_for(
        DEFAULT_COST_MAP, model,
        max_tokens=max_tokens, approx_input_tokens=approx_input_tokens,
    )
