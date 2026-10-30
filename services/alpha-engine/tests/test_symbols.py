import pytest
from yta_alpha.errors import DomainError
from yta_alpha.symbols import normalize_symbol


@pytest.mark.parametrize(
    "value,expected",
    [("nvda", "NVDA"), (" NVDA\t", "NVDA"), ("brk.b", "BRK.B"), ("BRK-B", "BRK-B")],
)
def test_normalize_ascii_symbol(value, expected):
    assert normalize_symbol(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "ＮＶＤＡ",
        "NⅤDA",
        "NV DA",
        "NVDA/..",
        "NVDA?x=1",
        "NVDA\x00",
        "1NVDA",
        "NVDA..A",
        "A" * 33,
        None,
    ],
)
def test_invalid_symbols_are_not_silently_rewritten(value):
    with pytest.raises(DomainError) as error:
        normalize_symbol(value)
    assert error.value.code == "INVALID_SYMBOL"


def test_shared_bff_symbol_examples_match_alpha_normalization():
    import json
    from pathlib import Path

    cases = json.loads(
        (
            Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/symbol-inputs.json"
        ).read_text()
    )
    for value in cases["accepted"]:
        assert normalize_symbol(value) == "NVDA"
    for value in cases["unsupported"]:
        assert normalize_symbol(value) != "NVDA"
    for value in cases["invalid"]:
        with pytest.raises(DomainError):
            normalize_symbol(value)
