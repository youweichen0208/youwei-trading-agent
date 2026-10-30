import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("prepare_pi_model_probe"), importlib.import_module(
        "probe_hermes_pi"
    )


def write_pi(root, **changes):
    route = {
        "api": "anthropic-messages",
        "baseUrl": "https://example.invalid/compatible/anthropic",
        "apiKey": "fixture-secret",
        "headers": {"Authorization": "Bearer fixture-secret"},
        "models": [{"id": "selected", "contextWindow": 1000000}],
        **changes,
    }
    (root / "models.json").write_text(
        json.dumps({"providers": {"chosen": route, "unused": {"apiKey": "other-secret"}}})
    )
    (root / "settings.json").write_text(
        json.dumps(
            {"defaultProvider": "chosen", "defaultModel": "selected", "defaultThinkingLevel": "off"}
        )
    )


def test_imports_only_selected_route_and_preserves_auth_and_thinking(tmp_path, modules):
    prepare, _ = modules
    write_pi(tmp_path)
    result = prepare.selected_route(tmp_path)
    assert result["api_key"] == "fixture-secret"
    assert result["headers"] == {"Authorization": "Bearer fixture-secret"}
    assert result["thinking"] == "off"
    assert result["api_mode"] == "anthropic_messages"
    assert result["model"] == "selected"
    assert "other-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"api": "unknown"},
        {"baseUrl": "http://example.invalid"},
        {"baseUrl": "https://user:password@example.invalid"},
        {"baseUrl": "https://example.invalid?api_key=secret"},
        {"apiKey": "!cat private-file"},
        {"headers": {"Authorization": "!run-command"}},
    ],
)
def test_import_rejects_unsupported_or_unsafe_route(tmp_path, modules, changes):
    prepare, _ = modules
    write_pi(tmp_path, **changes)
    with pytest.raises(ValueError):
        prepare.selected_route(tmp_path)


def test_real_model_probe_accepts_interleaved_correlated_tools(modules):
    _, probe = modules
    names = sorted(probe.TOOLS)
    events = [
        {"event": "tool.started", "tool": names[0], "tool_call_id": "a"},
        {"event": "tool.started", "tool": names[1], "tool_call_id": "b"},
        {"event": "tool.completed", "tool": names[1], "tool_call_id": "b", "error": False},
        {"event": "tool.completed", "tool": names[0], "tool_call_id": "a", "error": False},
        {"event": "run.completed"},
    ]
    events = [{**event, "seq": index} for index, event in enumerate(events)]
    assert probe.verify_events(events) == list(range(5))
    events[2]["tool_call_id"] = "different"
    with pytest.raises(probe.protocol.ProbeFailure, match="tool_ids"):
        probe.verify_events(events)
