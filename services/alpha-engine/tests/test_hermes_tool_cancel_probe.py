import importlib
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("probe_hermes_tool_cancel")


def events():
    tool = {"tool": "mcp__yta_cancel_probe__delayed_diagnostic", "tool_call_id": "call-test"}
    return [
        {**tool, "event": "tool.started", "seq": 0},
        {**tool, "event": "tool.completed", "seq": 1, "error": True},
        {"event": "run.cancelled", "seq": 2},
    ]


def test_accepts_correlated_interrupted_tool(probe):
    assert probe.validate_tool_events(events()) == [
        "tool.started",
        "tool.completed",
        "run.cancelled",
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_id",
        "missing_id",
        "success",
        "missing_error",
        "wrong_tool",
        "duplicate_seq",
        "completed_run",
    ],
)
def test_rejects_false_tool_cancellation_evidence(probe, damage):
    value = events()
    if damage == "wrong_id":
        value[1]["tool_call_id"] = "different"
    elif damage == "missing_id":
        value[0].pop("tool_call_id")
    elif damage == "success":
        value[1]["error"] = False
    elif damage == "missing_error":
        value[1].pop("error")
    elif damage == "wrong_tool":
        value[0]["tool"] = value[1]["tool"] = "unrelated"
    elif damage == "duplicate_seq":
        value[2]["seq"] = 1
    else:
        value[2]["event"] = "run.completed"
    with pytest.raises(probe.protocol.ProbeFailure):
        probe.validate_tool_events(value)
