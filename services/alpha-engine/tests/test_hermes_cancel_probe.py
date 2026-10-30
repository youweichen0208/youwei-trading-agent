import importlib
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("probe_hermes_cancel")


def test_cancel_events_require_output_and_cancelled_terminal(probe):
    events = [{"event": "message.delta", "seq": 0}, {"event": "run.cancelled", "seq": 1}]
    assert probe.validate_cancel_events(events) == [0, 1]


@pytest.mark.parametrize("damage", ["completed", "failed", "tool", "duplicate", "no_output"])
def test_cancel_probe_rejects_false_success(probe, damage):
    events = [{"event": "message.delta", "seq": 0}, {"event": "run.cancelled", "seq": 1}]
    if damage in ("completed", "failed"):
        events[-1]["event"] = "run." + damage
    elif damage == "tool":
        events.insert(1, {"event": "tool.started", "seq": 1})
        events[-1]["seq"] = 2
    elif damage == "duplicate":
        events[-1]["seq"] = 0
    else:
        events = events[-1:]
    with pytest.raises(probe.protocol.ProbeFailure):
        probe.validate_cancel_events(events)


def streaming_client(events):
    payload = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    )


def test_wait_observes_nonempty_model_output(probe):
    with streaming_client(
        [
            {"event": "message.delta", "delta": "", "seq": 0},
            {"event": "message.delta", "delta": "actual first fragment", "seq": 1},
        ]
    ) as client:
        assert probe.wait_for_output(client, "fixture") == 1


def test_wait_rejects_run_which_finishes_before_stop(probe):
    with streaming_client([{"event": "run.completed", "seq": 0}]) as client:
        with pytest.raises(probe.protocol.ProbeFailure, match="terminal_before_stop"):
            probe.wait_for_output(client, "fixture")
