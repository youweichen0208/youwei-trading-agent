from decimal import Decimal

import pytest
from pydantic import ValidationError
from yta_alpha.models import MarketBar


def bar(volume):
    return MarketBar(
        session="2026-10-29",
        open="1",
        high="2",
        low="1",
        close="2",
        volume=volume,
        available_at="2026-10-29T22:00:00Z",
        retrieved_at="2026-10-29T22:00:00Z",
        revision="test",
    )


def test_fractional_volume_roundtrips_without_binary_float_or_truncation():
    value = Decimal("98684721.771355")
    original = bar(value)
    assert original.model_dump(mode="json")["volume"] == "98684721.771355"
    assert MarketBar.model_validate_json(original.model_dump_json()).volume == value
    # Previously persisted integer-volume snapshot hashes remain reproducible.
    assert bar(100).model_dump(mode="json")["volume"] == 100


def test_fractional_volume_schema_uses_decimal_strings():
    branches = MarketBar.model_json_schema()["properties"]["volume"]["anyOf"]
    assert any(branch.get("type") == "string" for branch in branches)
    assert all(branch.get("type") != "number" for branch in branches)


@pytest.mark.parametrize("invalid", [True, 0.25, "-0.1", "NaN", "Infinity"])
def test_volume_rejects_ambiguous_or_invalid_numbers(invalid):
    with pytest.raises(ValidationError):
        bar(invalid)
