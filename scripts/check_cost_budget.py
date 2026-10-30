"""Read-only monthly budget review of explicitly supplied costs; never controls a provider."""

import argparse
import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")]


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    incurred: Money | None
    remaining_commitment: Money | None


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["yta-cost-budget-v1"]
    billing_month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    as_of: AwareDatetime
    currency: Literal["USD", "CNY"]
    monthly_limit: Money | None
    infrastructure: Costs
    model: Costs
    market_data: Costs
    storage_and_backup: Costs

    @field_validator("monthly_limit")
    @classmethod
    def positive_limit(cls, value):
        if value is not None and Decimal(value) <= 0:
            raise ValueError("Positive monthly limit required")
        return value


def evaluate(budget: Budget) -> dict:
    missing = []
    with localcontext() as context:
        context.prec = 50
        total = Decimal(0)
        for category in ("infrastructure", "model", "market_data", "storage_and_backup"):
            for field in ("incurred", "remaining_commitment"):
                value = getattr(getattr(budget, category), field)
                if value is None:
                    missing.append(f"{category}.{field}")
                else:
                    total += Decimal(value)
        limit = Decimal(budget.monthly_limit) if budget.monthly_limit is not None else None
        threshold = limit * Decimal("0.80") if limit is not None else None
        status = (
            "UNCONFIGURED"
            if limit is None
            else "LIMIT_REACHED"
            if total >= limit
            else "INCOMPLETE"
            if missing
            else "WARNING"
            if total >= threshold
            else "WITHIN_LIMIT"
        )
        return {
            "schema_version": "yta-cost-budget-review-v1",
            "billing_month": budget.billing_month,
            "as_of": budget.as_of.isoformat(),
            "currency": budget.currency,
            "status": status,
            "known_total": str(total),
            "monthly_limit": str(limit) if limit is not None else None,
            "warning_fraction": "0.80",
            "warning_threshold": str(threshold) if threshold is not None else None,
            "complete": not missing,
            "missing": missing,
            "automatic_enforcement": False,
            "source_verification": "CALLER_SUPPLIED_NOT_VERIFIED",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as file:
            raw = file.read(16385)
        if len(raw) > 16384:
            raise ValueError("Input too large")

        # Reject duplicate keys rather than silently accepting a different budget.
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate field")
                result[key] = value
            return result

        parsed = json.loads(raw, object_pairs_hook=pairs)
        budget = Budget.model_validate_json(json.dumps(parsed))
        result = evaluate(budget)
    except Exception:
        print(json.dumps({"error_code": "COST_BUDGET_INPUT_INVALID"}))
        return 2
    print(json.dumps(result))
    return 0 if result["status"] == "WITHIN_LIMIT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
