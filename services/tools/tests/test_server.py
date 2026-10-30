import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from pydantic import SecretStr, ValidationError
from yta_alpha.api import create_app
from yta_alpha.calendar import TradingCalendar
from yta_alpha.models import DataMode
from yta_alpha.settings import Settings as AlphaSettings
from yta_tools.server import Settings, create_server

TOKEN = "fixture-read-only-tools-credential-123456789"


def settings(mode=DataMode.REPLAY):
    return Settings(alpha_url="http://alpha", alpha_token=SecretStr(TOKEN), data_mode=mode)


@pytest.fixture(scope="module")
def alpha():
    app = create_app(
        AlphaSettings(
            _env_file=None,
            alpha_token=SecretStr("fixture-service-token-distinct-123456789"),
            alpha_tools_token=SecretStr(TOKEN),
        ),
        calendar=TradingCalendar(),
    )
    return httpx.ASGITransport(app=app)


def test_real_mcp_protocol_to_alpha_reads(alpha):
    async def run():
        async with Client(create_server(settings(), alpha)) as client:
            tools = (await client.list_tools()).tools
            assert {t.name for t in tools} == {"market_snapshot", "indicators"}
            assert all(t.input_schema["additionalProperties"] is False for t in tools)
            assert all(t.annotations.read_only_hint for t in tools)
            market = await client.call_tool("market_snapshot", {})
            chart = await client.call_tool("indicators", {})
            assert not market.is_error, market.content
            assert not chart.is_error, chart.content
            snapshot = market.structured_content["snapshot"]
            assert snapshot["data_mode"] == "REPLAY"
            assert isinstance(snapshot["bars"][0]["close"], str)
            assert chart.structured_content["series"]["SMA20"][0] is None
            assert (
                chart.structured_content["data_snapshot_hash"]
                == market.structured_content["data_snapshot_hash"]
            )

    asyncio.run(run())


def test_unknown_tools_and_model_supplied_privileges_do_not_call_alpha():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async def run():
        async with Client(create_server(settings(), httpx.MockTransport(handler))) as client:
            for name, args in [
                ("freeze_prediction", {}),
                ("market_snapshot", {"owner_id": "admin"}),
                ("indicators", {"url": "http://elsewhere", "data_mode": "LIVE_RESEARCH"}),
            ]:
                result = await client.call_tool(name, args)
                assert result.is_error
            assert calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "ALPHA_AUTH_FAILED"),
        (503, "DATA_UNAVAILABLE"),
        (302, "ALPHA_REQUEST_FAILED"),
        (500, "ALPHA_REQUEST_FAILED"),
    ],
)
def test_upstream_errors_are_redacted_and_redirects_not_followed(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(status, text=TOKEN, headers={"location": "http://elsewhere"})

    async def run():
        async with Client(create_server(settings(), httpx.MockTransport(handler))) as client:
            result = await client.call_tool("market_snapshot", {})
            assert result.is_error and result.content[0].text == code
            assert TOKEN not in result.model_dump_json()
            assert len(calls) == 1

    asyncio.run(run())


def test_mode_mismatch_cannot_be_reported_as_live(alpha):
    async def run():
        async with Client(create_server(settings(DataMode.LIVE_RESEARCH), alpha)) as client:
            for name in ("market_snapshot", "indicators"):
                result = await client.call_tool(name, {})
                assert result.is_error and result.content[0].text == "DATA_MODE_MISMATCH"

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/secret", "http://user:secret@alpha", "http://alpha/path", "http://alpha?x=1"],
)
def test_only_configured_origin_allowed(url):
    with pytest.raises(ValidationError):
        Settings(alpha_url=url, alpha_token=SecretStr(TOKEN), data_mode="REPLAY")


@pytest.mark.parametrize(
    "content,headers,code",
    [
        (b"not-json", {}, "INVALID_ALPHA_RESPONSE"),
        (b"[]", {}, "INVALID_ALPHA_RESPONSE"),
        (b"{}", {"content-encoding": "gzip"}, "INVALID_ALPHA_RESPONSE"),
        (b"x" * (2 * 1024 * 1024 + 1), {}, "ALPHA_RESPONSE_TOO_LARGE"),
    ],
)
def test_invalid_or_oversized_response_is_not_a_tool_success(content, headers, code):
    def handler(request):
        return httpx.Response(
            200, content=content, headers={"content-type": "application/json", **headers}
        )

    async def run():
        async with Client(create_server(settings(), httpx.MockTransport(handler))) as client:
            result = await client.call_tool("market_snapshot", {})
            assert result.is_error and result.content[0].text == code
            assert result.structured_content is None

    asyncio.run(run())


def test_network_failure_is_sanitized():
    def handler(request):
        raise httpx.ReadTimeout(TOKEN)

    async def run():
        async with Client(create_server(settings(), httpx.MockTransport(handler))) as client:
            result = await client.call_tool("market_snapshot", {})
            assert result.is_error and result.content[0].text == "ALPHA_UNAVAILABLE"
            assert TOKEN not in result.model_dump_json()

    asyncio.run(run())


def test_total_deadline_is_bounded(monkeypatch):
    import yta_tools.server as module

    monkeypatch.setattr(module, "REQUEST_DEADLINE", 0.05)

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    async def run():
        async with Client(create_server(settings(), httpx.MockTransport(handler))) as client:
            result = await client.call_tool("market_snapshot", {})
            assert result.is_error and result.content[0].text == "ALPHA_UNAVAILABLE"

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["float", "nan", "misalignment", "naive_time", "bad_hash"])
def test_indicator_contract_rejects_corrupt_wire_data(fault):
    path = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/chart.json"
    body = json.loads(path.read_text())
    if fault == "float":
        body["bars"][0]["close"] = 100.1
    elif fault == "nan":
        body["series"]["SMA20"][0] = "NaN"
    elif fault == "misalignment":
        body["sessions"].pop()
    elif fault == "naive_time":
        body["as_of"] = "2024-11-22T12:00:00"
    else:
        body["data_snapshot_hash"] = "invalid"

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        async with Client(create_server(settings(), transport)) as client:
            result = await client.call_tool("indicators", {})
            assert result.is_error and result.content[0].text == "INVALID_ALPHA_RESPONSE"

    asyncio.run(run())


def test_stdio_process_to_real_http_alpha(alpha):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(alpha.app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "yta_tools.server"],
                env={
                    "YTA_TOOLS_ALPHA_URL": f"http://127.0.0.1:{port}",
                    "YTA_TOOLS_ALPHA_TOKEN": TOKEN,
                    "YTA_TOOLS_DATA_MODE": "REPLAY",
                },
            )
            async with Client(params, read_timeout_seconds=10) as client:
                assert {t.name for t in (await client.list_tools()).tools} == {
                    "market_snapshot",
                    "indicators",
                }
                for name in ("market_snapshot", "indicators"):
                    result = await client.call_tool(name, {})
                    assert not result.is_error, result.content
                    assert result.structured_content["data_snapshot_hash"]

        asyncio.run(run())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "mutation", ["context", "identity", "tool", "mode", "hash", "cutoff", "extra"]
)
def test_scoped_response_binding_rejects_upstream_mismatch(alpha, mutation):
    from uuid import uuid4

    from yta_alpha.forecast import snapshot_hash
    from yta_alpha.models import MarketSnapshot
    from yta_alpha.research_tools import ResearchTools
    from yta_tools.server import ToolFailure, fetch

    async def run():
        async with httpx.AsyncClient(transport=alpha, base_url="http://alpha") as client:
            response = await client.get(
                "/internal/v1/tools/market-snapshot", headers={"Authorization": f"Bearer {TOKEN}"}
            )
        result = response.json()
        snapshot = MarketSnapshot.model_validate(result["snapshot"])
        context = uuid4()

        def handler(request):
            assert request.method == "POST"
            assert request.url.path == "/internal/v1/tools/observations"
            body = json.loads(request.content)
            payload = {
                "schema_version": "research-tool-result-v1",
                "tool_call_id": body["request_id"],
                "tool": "market_snapshot",
                **ResearchTools.summary(context, snapshot),
                "result": result,
            }
            assert payload["data_snapshot_hash"] == snapshot_hash(snapshot)
            changes = {
                "context": ("context_id", str(uuid4())),
                "identity": ("tool_call_id", str(uuid4())),
                "tool": ("tool", "indicators"),
                "mode": ("data_mode", "LIVE_RESEARCH"),
                "hash": ("data_snapshot_hash", "a" * 64),
                "cutoff": ("as_of", "2099-01-01T00:00:00Z"),
                "extra": ("owner_id", str(uuid4())),
            }
            key, value = changes[mutation]
            payload[key] = value
            return httpx.Response(200, json=payload)

        with pytest.raises(ToolFailure, match="INVALID_ALPHA_RESPONSE"):
            await fetch("market_snapshot", settings(), httpx.MockTransport(handler), context)

    asyncio.run(run())
