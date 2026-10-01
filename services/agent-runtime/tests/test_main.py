"""S07: research-once stdout discipline (the Hermes banner must not leak into
the wire response).

Hermes writes its init banner / API progress / conversation log via bare
print() to stdout (agent/agent_init.py, turn_facade.py). The research-once
wire contract is a single JSON object on stdout, so main._research_once
diverts stdout to stderr for the duration of the turn and only restores the
real stdout to emit the result/error. These tests pin that discipline with a
fake honor_request (no Hermes import).
"""

import asyncio
import io
import json

import pytest

from youwei_agent_runtime import main


class _Args:
    capability_secret_env = "YOUWEI_CAPABILITY_SECRET"


async def _fake_honor_request(payload, *, capability_secret, run_research, config_factory):
    # Simulate Hermes's stdout noise emitted during the turn.
    print("🤖 AI Agent initialized")
    print("🔗 Using custom base URL")
    return '{"ok":true,"proposal":{"source_status":"produced"}}'


async def _fake_honor_request_error(payload, *, capability_secret, run_research, config_factory):
    print("🤖 AI Agent initialized")
    raise main.InvocationError("capability rejected: signature mismatch")


def _run(handler, monkeypatch, request_text='{"capability_token":"ywc_x","evidence":{},"config":{}}'):
    monkeypatch.setattr(main, "_capability_secret", lambda args: "secret")
    monkeypatch.setattr(main, "_register_research_tools", lambda: None)
    monkeypatch.setattr(main, "honor_request", handler)
    monkeypatch.setattr("sys.stdin", io.StringIO(request_text))

    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)

    code = asyncio.run(main._research_once(_Args()))
    return code, real_stdout.getvalue(), real_stderr.getvalue()


def test_result_is_the_only_stdout(monkeypatch):
    code, stdout, stderr = _run(_fake_honor_request, monkeypatch)
    # stdout carries exactly one JSON object and nothing else.
    parsed = json.loads(stdout)
    assert parsed == {"ok": True, "proposal": {"source_status": "produced"}}
    assert code == 0
    # Hermes's banner noise went to stderr, not stdout.
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
    # After _research_once returns, sys.stdout must be restored so the process
    # does not leak the redirect into any later writes.
    real_stdout = io.StringIO()
    real_stderr = io.StringIO()
    monkeypatch.setattr(main, "_capability_secret", lambda args: "secret")
    monkeypatch.setattr(main, "_register_research_tools", lambda: None)
    monkeypatch.setattr(main, "honor_request", _fake_honor_request)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"x":1}'))
    monkeypatch.setattr("sys.stdout", real_stdout)
    monkeypatch.setattr("sys.stderr", real_stderr)

    asyncio.run(main._research_once(_Args()))

    import sys

    assert sys.stdout is real_stdout
    assert sys.stderr is real_stderr
