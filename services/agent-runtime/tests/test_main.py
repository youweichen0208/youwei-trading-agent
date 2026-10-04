"""S07m: research-once stdout discipline (the Hermes banner must not leak into
the wire response).

Hermes writes its init banner / API progress / conversation log via bare
print() to stdout (agent/agent_init.py, turn_facade.py). The research-once
wire contract is a single JSON object on stdout, so main._research_once
diverts stdout to stderr for the duration of the turn and only restores the
real stdout to emit the result/error. These tests pin that discipline (plus
the stdin/stdout byte caps) with a fake honor_request (no Hermes import).
"""

import asyncio
import io
import json

import pytest

from youwei_agent_runtime import main


class _Args:
    public_keys_env = "YOUWEI_RESEARCH_PUBLIC_KEYS"


class _BufferedStdin:
    """A minimal stdin stand-in exposing .buffer like the real sys.stdin."""

    def __init__(self, text: str):
        self.buffer = io.BytesIO(text.encode("utf-8"))


async def _fake_honor_request(payload, *, public_keys, run_research, config_factory):
    # Simulate Hermes's stdout noise emitted during the turn.
    print("🤖 AI Agent initialized")
    print("🔗 Using custom base URL")
    return '{"ok":true,"proposal":{"source_status":"produced"}}'


async def _fake_honor_request_error(payload, *, public_keys, run_research, config_factory):
    print("🤖 AI Agent initialized")
    raise main.InvocationError("capability rejected: signature mismatch")


def _run(handler, monkeypatch, request_text='{"capability_token":"ywr_x","evidence":{},"config":{}}'):
    monkeypatch.setattr(main, "_public_keys", lambda args: {"k1": "pem"})
    monkeypatch.setattr(main, "_register_research_tools", lambda: None)
    monkeypatch.setattr(main, "honor_request", handler)
    monkeypatch.setattr("sys.stdin", _BufferedStdin(request_text))

    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)

    code = asyncio.run(main._research_once(_Args()))
    return code, real_stdout.getvalue(), real_stderr.getvalue()


def test_result_is_the_only_stdout(monkeypatch):
    code, stdout, stderr = _run(_fake_honor_request, monkeypatch)
    parsed = json.loads(stdout)
    assert parsed == {"ok": True, "proposal": {"source_status": "produced"}}
    assert code == 0
    assert "AI Agent initialized" in stderr
    assert "AI Agent initialized" not in stdout


def test_error_is_written_to_stdout(monkeypatch):
    code, stdout, stderr = _run(_fake_honor_request_error, monkeypatch)
    parsed = json.loads(stdout)
    assert parsed.get("ok") is False
    assert "signature mismatch" in parsed.get("error", "")
    assert code == 2
    assert "AI Agent initialized" in stderr
    assert "AI Agent initialized" not in stdout


def test_stdout_is_restored_after_turn(monkeypatch):
    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr(main, "_public_keys", lambda args: {"k1": "pem"})
    monkeypatch.setattr(main, "_register_research_tools", lambda: None)
    monkeypatch.setattr(main, "honor_request", _fake_honor_request)
    monkeypatch.setattr("sys.stdin", _BufferedStdin('{"x":1}'))
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)

    asyncio.run(main._research_once(_Args()))

    import sys

    assert sys.stdout is real_stdout
    assert sys.stderr is real_stderr


def test_stdin_over_cap_is_rejected(monkeypatch):
    # A request larger than MAX_STDIN_BYTES fails rather than being truncated.
    monkeypatch.setattr(main, "_public_keys", lambda args: {"k1": "pem"})
    monkeypatch.setattr(main, "_register_research_tools", lambda: None)
    monkeypatch.setattr(main, "honor_request", _fake_honor_request)
    big = '{"capability_token":"' + "a" * (main.MAX_STDIN_BYTES + 1) + '"}'
    monkeypatch.setattr("sys.stdin", _BufferedStdin(big))
    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)
    code = asyncio.run(main._research_once(_Args()))
    assert code == 2
    parsed = json.loads(real_stdout.getvalue())
    assert parsed.get("ok") is False
    assert "stdin byte cap" in parsed.get("error", "")


# --- experiment-once (S08c-2) ---------------------------------------------------


async def _fake_honor_experiment_request(payload, *, public_keys, run_experiment_fn, config_factory):
    print("🤖 AI Agent initialized")
    return '{"ok":true,"result":{"findings":"ratio at median"},"usage":{"source":"x"}}'


async def _fake_honor_experiment_error(payload, *, public_keys, run_experiment_fn, config_factory):
    print("🤖 AI Agent initialized")
    raise main.ExperimentInvocationError("experiment capability rejected")


def _run_experiment(handler, monkeypatch, request_text='{"capability_token":"ywr_x","request":{},"gateway":{}}'):
    monkeypatch.setattr(main, "_public_keys", lambda args: {"k1": "pem"})
    monkeypatch.setattr(main, "_register_experiment_tools", lambda: None)
    monkeypatch.setattr(main, "honor_experiment_request", handler)
    monkeypatch.setattr("sys.stdin", _BufferedStdin(request_text))

    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)

    code = asyncio.run(main._experiment_once(_Args()))
    return code, real_stdout.getvalue(), real_stderr.getvalue()


def test_experiment_result_is_the_only_stdout(monkeypatch):
    code, stdout, stderr = _run_experiment(_fake_honor_experiment_request, monkeypatch)
    assert code == 0
    payload = json.loads(stdout.strip())
    assert payload["ok"] is True
    assert payload["result"]["findings"] == "ratio at median"
    # the banner went to stderr, never stdout
    assert "AI Agent initialized" in stderr
    assert "AI Agent initialized" not in stdout


def test_experiment_error_goes_to_stdout_with_code(monkeypatch):
    code, stdout, stderr = _run_experiment(_fake_honor_experiment_error, monkeypatch)
    assert code == 2
    payload = json.loads(stdout.strip())
    assert payload["ok"] is False
    assert "experiment capability rejected" in payload["error"]
    assert "AI Agent initialized" in stderr


def test_experiment_non_json_request_fails(monkeypatch):
    code, stdout, _ = _run_experiment(
        _fake_honor_experiment_request, monkeypatch, request_text="not json"
    )
    assert code == 2
    payload = json.loads(stdout.strip())
    assert payload["ok"] is False
