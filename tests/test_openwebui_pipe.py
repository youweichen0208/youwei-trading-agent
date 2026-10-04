"""Open WebUI research pipe adapter — offline tests against a mock Core.

The adapter is the THIN S12c layer: parse / submit / poll / summarize /
cancel. All semantics come from Core (S12a); these tests pin the message
contract, the idempotency binding, the bounded polling and the honest
interim/failure renders. No Open WebUI, no network.
"""

import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integrations" / "openwebui"))

from youwei_research_pipe import Pipe  # noqa: E402

RID = "11111111-2222-3333-4444-555555555555"
KEY = "ywa-test-key"


def _pipe(**valves) -> Pipe:
    p = Pipe()
    base = {"core_url": "http://core.test", "core_api_key": KEY,
            "poll_interval_seconds": 0.1, "poll_timeout_seconds": 5.0}
    base.update(valves)
    p.valves = p.Valves(**base)
    return p


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://core.test"
    )


def _body(text):
    return {"model": "youwei", "messages": [{"role": "user", "content": text}]}


def _task(status="pending", **over):
    task = {
        "research_id": RID, "ticker": "S0", "benchmark_ticker": "SPY",
        "horizon_td": 20, "status": status, "job_status": "queued",
    }
    task.update(over)
    return task


def _report():
    return {
        "research_id": RID, "report_version": 1, "is_latest": True,
        "content": {
            "summary": {"source_status": "produced", "p_outperform": 0.55,
                        "expected_excess_return": 0.02, "quant_relation": "adjusted"},
            "quant": {"quant_model": {"source_status": "produced", "p_outperform": 0.61,
                                      "model_version": "quant-momentum-v0", "reason": None},
                      "baseline": {}},
            "counter_evidence": {"warnings": [{"kind": "low_confidence", "detail": "thin volume"}],
                                 "missing": ["sector context"], "quantitative_basis": "momentum overstates"},
            "limitations": ["exploratory research: not a sealed prediction"],
        },
    }


# --- message parsing ---------------------------------------------------------


def test_parse_research_status_cancel_and_help():
    assert Pipe.parse_message("研究 AAPL D20", 20) == {
        "action": "research", "ticker": "AAPL", "horizon_td": 20}
    assert Pipe.parse_message("research aapl", 20) == {
        "action": "research", "ticker": "AAPL", "horizon_td": 20}
    assert Pipe.parse_message("研究 BRK.B D1", 20)["horizon_td"] == 1
    assert Pipe.parse_message(f"状态 {RID}", 20)["action"] == "status"
    assert Pipe.parse_message(f"cancel {RID}", 20)["action"] == "cancel"
    assert Pipe.parse_message("你好", 20)["action"] == "help"
    assert Pipe.parse_message("", 20)["action"] == "help"
    with pytest.raises(ValueError):
        Pipe.parse_message("研究 !!BAD!!", 20)
    with pytest.raises(ValueError):
        Pipe.parse_message("研究 AAPL D7", 20)


def test_idempotency_key_binds_user_and_text():
    user = {"id": "u1"}
    a = Pipe.idempotency_key(user, "研究 AAPL D20")
    assert a == Pipe.idempotency_key(user, "研究 AAPL D20")
    assert a != Pipe.idempotency_key({"id": "u2"}, "研究 AAPL D20")
    assert a != Pipe.idempotency_key(user, "研究 AAPL D60")
    assert a.startswith("owui:u1:")


# --- pipe flows (mock Core) --------------------------------------------------


async def test_research_success_renders_report_and_detail_link():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/research":
            assert json.loads(request.content) == {"ticker": "AAPL", "horizon_td": 20}
            assert request.headers["Idempotency-Key"].startswith("owui:")
            assert request.headers["Authorization"] == f"Bearer {KEY}"
            return httpx.Response(201, json=_task(status="succeeded"))
        if request.url.path == f"/v1/research/{RID}/report":
            return httpx.Response(200, json=_report())
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    p = _pipe()
    out = await p.pipe(_body("研究 AAPL D20"), __user__={"id": "u1"}, _client=_client(handler))
    assert "p_outperform=0.55" in out
    assert "adjusted" in out
    assert "quant-momentum-v0" in out
    assert "momentum overstates" in out
    assert "thin volume" in out
    assert "sector context" in out
    assert "not a sealed prediction" in out
    assert f"#/research/{RID}" in out
    assert "[object Object]" not in out and "None" not in out


async def test_poll_until_terminal_with_bounded_wait():
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=_task(status="pending"))
        if request.url.path == f"/v1/research/{RID}/report":
            return httpx.Response(200, json=_report())
        polls["n"] += 1
        status = "running" if polls["n"] < 3 else "succeeded"
        return httpx.Response(200, json=_task(status=status))

    p = _pipe(poll_interval_seconds=0.1, poll_timeout_seconds=5.0)
    out = await p.pipe(_body("研究 AAPL"), __user__={"id": "u1"}, _client=_client(handler))
    assert polls["n"] >= 3
    assert "p_outperform=0.55" in out


async def test_poll_timeout_returns_interim_state_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=_task(status="running"))
        return httpx.Response(200, json=_task(status="running"))

    p = _pipe(poll_interval_seconds=0.1, poll_timeout_seconds=0.5)
    out = await p.pipe(_body("研究 AAPL"), __user__={"id": "u1"}, _client=_client(handler))
    assert "仍在执行" in out
    assert f"状态 {RID}" in out  # how to follow up
    assert f"#/research/{RID}" in out


async def test_failed_and_cancelled_render_honestly():
    def handler(status):
        def h(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(201, json=_task(status=status))
            return httpx.Response(200, json=_task(status=status))
        return h

    p = _pipe()
    out = await p.pipe(_body("研究 AAPL"), __user__={"id": "u1"}, _client=_client(handler("failed")))
    assert "研究未完成：failed" in out
    out = await p.pipe(_body("研究 AAPL"), __user__={"id": "u1"}, _client=_client(handler("cancelled")))
    assert "研究未完成：cancelled" in out


async def test_status_and_cancel_commands():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v1/research/{RID}":
            return httpx.Response(200, json=_task(status="running"))
        if request.url.path == f"/v1/research/{RID}/cancel":
            return httpx.Response(200, json={"research_id": RID, "status": "cancelled",
                                             "already_terminal": False})
        raise AssertionError(request.url.path)

    p = _pipe()
    out = await p.pipe(_body(f"状态 {RID}"), __user__={"id": "u1"}, _client=_client(handler))
    assert "running" in out and "D20" in out
    out = await p.pipe(_body(f"取消 {RID}"), __user__={"id": "u1"}, _client=_client(handler))
    assert "cancelled" in out


async def test_core_errors_are_explained_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"detail": {"error": "ticker 'NOPE' does not resolve"}})
        return httpx.Response(404, json={"detail": "research not found"})

    p = _pipe()
    out = await p.pipe(_body("研究 NOPE"), __user__={"id": "u1"}, _client=_client(handler))
    assert "请求失败" in out and "NOPE" in out
    out = await p.pipe(_body(f"状态 {RID}"), __user__={"id": "u1"}, _client=_client(handler))
    assert "未找到" in out


async def test_resend_same_message_shows_existing_state():
    """Refresh/reconnect safety: the same user text maps to the same
    idempotency key, so a resend hits the existing research and renders
    its CURRENT state — never a silent duplicate."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=_task(status="succeeded"))  # replay: 200, exists

    p = _pipe()

    def report_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research":
            return httpx.Response(200, json=_task(status="succeeded"))
        return httpx.Response(200, json=_report())

    out = await p.pipe(_body("研究 AAPL D20"), __user__={"id": "u1"}, _client=_client(report_handler))
    assert "p_outperform=0.55" in out


async def test_missing_key_is_a_clear_configuration_error():
    """A missing Core key renders as a chat-side configuration message,
    not a raw stack trace in the UI."""
    p = _pipe(core_api_key="")
    out = await p.pipe(_body("研究 AAPL"), __user__={"id": "u1"}, _client=_client(lambda r: None))
    assert "配置错误" in out and "key" in out


async def test_help_and_unparsable_research():
    p = _pipe()
    out = await p.pipe(_body("帮我看看市场"), __user__={"id": "u1"}, _client=_client(lambda r: None))
    assert "用法" in out
    out = await p.pipe(_body("研究"), __user__={"id": "u1"}, _client=_client(lambda r: None))
    assert "用法" in out or "无法解析" in out


async def test_background_tasks_never_trigger_research():
    """Open WebUI routes title/tag/follow-up generation through the selected
    model with ``__task__`` set (metadata.task). The pipe must short-circuit:
    no Core call, no research submission — a title derived from the first
    user message is echoed instead."""
    pipe = Pipe()
    called = []

    class Probe:
        async def post(self, *a, **kw):
            called.append(a)
            raise AssertionError("background task must not call Core")

    result = await pipe.pipe(
        {
            "messages": [
                {"role": "system", "content": "generate a title"},
                {"role": "user", "content": "研究 SPY D20"},
            ]
        },
        __user__={"id": "u1"},
        __task__="title_generation",
        _client=Probe(),
    )
    assert result == "研究 SPY D20"
    assert called == []


async def test_background_task_without_user_message():
    pipe = Pipe()

    class Probe:
        async def post(self, *a, **kw):
            raise AssertionError("background task must not call Core")

    result = await pipe.pipe(
        {"messages": [{"role": "system", "content": "x"}]},
        __user__={"id": "u1"},
        __task__="tags_generation",
        _client=Probe(),
    )
    assert result == "研究对话"
