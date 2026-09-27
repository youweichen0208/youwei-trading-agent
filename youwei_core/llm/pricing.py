"""Model pricing for the LLM gateway path.

Amounts are micros (micro-USD) per token. These are CONFIGURED
PLACEHOLDER values: they must be reconciled against actual Volcengine
console billing before any production campaign (see
docs/research/llm-gateway-options.md, completion checklist item 3).
The estimate function is deliberately conservative: it prices the
full max_tokens budget so a reservation always covers the worst case.
"""

from typing import Mapping

# 占位价（micros/token）：正式运行前须与火山控制台计费对账后替换。
MODEL_PRICES: Mapping[str, Mapping[str, int]] = {
    "glm-5.3": {"input": 10, "output": 40},
    "glm-5.3-flash": {"input": 2, "output": 8},
    "deepseek-v4-pro": {"input": 8, "output": 32},
    "deepseek-v4-flash": {"input": 1, "output": 4},
    "qwen3.8-flash": {"input": 1, "output": 4},
}


class UnknownModel(Exception):
    pass


def _prices(model: str) -> Mapping[str, int]:
    try:
        return MODEL_PRICES[model]
    except KeyError:
        raise UnknownModel(f"no price configured for model {model!r}") from None


def estimate_max_cost(model: str, *, max_tokens: int, approx_input_tokens: int) -> int:
    """Conservative maximum cost for one call: the full output budget
    plus the approximate input, both priced at list rates."""
    p = _prices(model)
    return approx_input_tokens * p["input"] + max_tokens * p["output"]


def actual_cost(model: str, usage: Mapping[str, int]) -> int:
    """Actual cost from the response usage block (OpenAI format)."""
    p = _prices(model)
    prompt = int(usage.get("prompt_tokens", 0))
    completion = int(usage.get("completion_tokens", 0))
    # cache reads (when reported) bill at input rate; refinement comes
    # with the gateway billing reconciliation
    return prompt * p["input"] + completion * p["output"]
