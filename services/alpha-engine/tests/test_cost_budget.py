import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("check_cost_budget")


@pytest.fixture
def data():
    return {
        "schema_version": "yta-cost-budget-v1",
        "billing_month": "2026-10",
        "as_of": "2026-10-29T00:00:00Z",
        "currency": "USD",
        "monthly_limit": "100",
        **{
            name: {"incurred": "0", "remaining_commitment": "0"}
            for name in ("infrastructure", "model", "market_data", "storage_and_backup")
        },
    }


@pytest.mark.parametrize(
    "cost,status",
    [
        ("79.999999", "WITHIN_LIMIT"),
        ("80", "WARNING"),
        ("99.999999", "WARNING"),
        ("100", "LIMIT_REACHED"),
    ],
)
def test_exact_warning_and_limit_boundaries(module, data, cost, status):
    data["model"]["incurred"] = cost
    result = module.evaluate(module.Budget.model_validate_json(json.dumps(data)))
    assert result["status"] == status
    assert result["automatic_enforcement"] is False


def test_commitments_count_and_missing_costs_never_mean_zero(module, data):
    data["model"] = {"incurred": "60.000001", "remaining_commitment": "20.000001"}

    def budget():
        return module.Budget.model_validate_json(json.dumps(data))

    assert module.evaluate(budget())["known_total"] == "80.000002"
    assert module.evaluate(budget())["status"] == "WARNING"
    data["market_data"]["incurred"] = None
    result = module.evaluate(budget())
    assert result["status"] == "INCOMPLETE" and result["complete"] is False
    assert result["missing"] == ["market_data.incurred"]
    data["model"]["incurred"] = "100"
    assert module.evaluate(budget())["status"] == "LIMIT_REACHED"
    data["monthly_limit"] = None
    assert module.evaluate(budget())["status"] == "UNCONFIGURED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "EUR"),
        ("monthly_limit", 100),
        ("monthly_limit", "-1"),
        ("monthly_limit", "NaN"),
        ("monthly_limit", "0"),
        ("billing_month", "2026-13"),
        ("as_of", "2026-10-29"),
    ],
)
def test_invalid_input_is_redacted(module, data, tmp_path, capsys, field, value):
    data[field] = value
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(data))
    assert module.main(["--input", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error_code": "COST_BUDGET_INPUT_INVALID"}


def test_cli_status_and_duplicate_key_rejection(module, data, tmp_path, capsys):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(data))
    assert module.main(["--input", str(path)]) == 0
    capsys.readouterr()
    path.write_text(
        json.dumps(data).replace(
            '"monthly_limit": "100"', '"monthly_limit": "100", "monthly_limit": "999"'
        )
    )
    assert module.main(["--input", str(path)]) == 2
    assert "999" not in capsys.readouterr().out
    path.write_text("private-token-" * 2000)
    assert module.main(["--input", str(path)]) == 2
    assert "private-token" not in capsys.readouterr().out
