import importlib
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("probe_hermes_gateway_context")


@pytest.mark.parametrize("side", ["left", "right"])
def test_context_fixture_requires_own_system_identity(probe, side):
    body = {
        "messages": [
            {"role": "system", "content": probe.MARKERS[side]},
            {"role": "user", "content": f"Check isolated context for {side}."},
        ]
    }
    message, finish = probe.answer(body)
    assert message["content"] == "CONTEXT_OK_" + side and finish == "stop"


@pytest.mark.parametrize("fault", ["missing", "foreign", "skill", "tool", "user_only"])
def test_context_fixture_rejects_leaks_and_missing_positive_control(probe, fault):
    body = {
        "messages": [
            {"role": "system", "content": probe.MARKERS["left"]},
            {"role": "user", "content": "Check isolated context for left."},
        ]
    }
    if fault == "missing":
        body["messages"][0]["content"] = ""
    elif fault == "foreign":
        body["messages"][0]["content"] += probe.MARKERS["right"]
    elif fault == "skill":
        body["messages"][0]["content"] += probe.SKILL_MARKER
    elif fault == "tool":
        body["tools"] = [{"type": "function", "function": {"name": "read_file"}}]
    else:
        body["messages"][0]["role"] = "user"
    with pytest.raises(ValueError):
        probe.answer(body)
