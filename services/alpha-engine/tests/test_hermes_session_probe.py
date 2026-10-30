import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("probe_hermes_session")


def test_recall_requests_do_not_supply_the_answer_or_history(probe):
    receipt = probe.make_receipt("fixture-model")
    assert receipt["marker"] in receipt["turns"]["seed"]["body"]["input"]
    for phase in ("recall", "isolated", "restart"):
        body = receipt["turns"][phase]["body"]
        assert set(body) == {"model", "input"}
        assert receipt["marker"] not in json.dumps(body)
    assert receipt["session_key"] != receipt["isolated_key"]
    assert len({turn["key"] for turn in receipt["turns"].values()}) == 4


def test_each_probe_has_independent_unpredictable_context(probe):
    first, second = probe.make_receipt("fixture"), probe.make_receipt("fixture")
    assert first["marker"] != second["marker"]
    assert first["session_key"] != second["session_key"]
    assert first["isolated_key"] != second["isolated_key"]
    assert not {turn["key"] for turn in first["turns"].values()}.intersection(
        turn["key"] for turn in second["turns"].values()
    )
