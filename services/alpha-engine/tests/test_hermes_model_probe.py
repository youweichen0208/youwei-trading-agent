import copy
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("hermes_model_fixture"), importlib.import_module(
        "probe_hermes_model"
    )


def test_fixture_requires_correlated_tool_results_before_advancing(modules):
    fixture, _ = modules
    body = {
        "model": fixture.MODEL,
        "tools": [{"function": {"name": name}} for name in fixture.TOOLS],
        "messages": [{"role": "user", "content": "synthetic probe"}],
    }
    first, reason = fixture.answer(body)
    assert reason == "tool_calls"
    assert first["tool_calls"][0]["function"]["name"] == fixture.TOOLS[0]
    body["messages"].append(first)
    assert fixture.answer(body)[0] == first
    body["messages"].append({"role": "tool", "tool_call_id": "unrelated", "content": "{}"})
    assert fixture.answer(body)[0] == first
    body["messages"].append({"role": "tool", "tool_call_id": "call_yta_fixture_0", "content": "{}"})
    second, reason = fixture.answer(body)
    assert second["tool_calls"][0]["function"]["name"] == fixture.TOOLS[1]
    body["messages"].extend(
        [second, {"role": "tool", "tool_call_id": "call_yta_fixture_1", "content": "{}"}]
    )
    result, reason = fixture.answer(body)
    assert reason == "stop" and result["content"] == fixture.TEXT


def test_fixture_rejects_other_model(modules):
    fixture, _ = modules
    with pytest.raises(ValueError):
        fixture.answer({"model": "real-model", "messages": []})


def events_for(probe):
    events = []
    for index, name in enumerate(probe.TOOLS):
        events.extend(
            [
                {"event": "tool.started", "tool": name, "tool_call_id": str(index)},
                {
                    "event": "tool.completed",
                    "tool": name,
                    "tool_call_id": str(index),
                    "error": False,
                },
            ]
        )
    events.append({"event": "run.completed"})
    return [{**event, "seq": index} for index, event in enumerate(events)]


def test_probe_accepts_correlated_success(modules):
    _, probe = modules
    assert probe.validate_events(events_for(probe)) == list(range(5))


@pytest.mark.parametrize(
    "damage",
    ["no_tools", "tool_error", "missing_error", "wrong_id", "duplicate_seq", "failed_terminal"],
)
def test_probe_does_not_mistake_completion_for_tool_success(modules, damage):
    _, probe = modules
    events = copy.deepcopy(events_for(probe))
    if damage == "no_tools":
        events = events[-1:]
    elif damage == "tool_error":
        events[1]["error"] = True
    elif damage == "missing_error":
        del events[1]["error"]
    elif damage == "wrong_id":
        events[1]["tool_call_id"] = "other"
    elif damage == "duplicate_seq":
        events[1]["seq"] = 0
    else:
        events[-1]["event"] = "run.failed"
    with pytest.raises(probe.protocol.ProbeFailure):
        probe.validate_events(events)
