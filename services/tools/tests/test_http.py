import asyncio
import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr, ValidationError
from starlette.testclient import TestClient
from yta_alpha.api import create_app
from yta_alpha.calendar import TradingCalendar
from yta_alpha.settings import Settings as AlphaSettings
from yta_tools.http import HTTPSettings, create_http_app

TOKEN = "fixture-mcp-transport-token-distinct-123456789"
ALPHA_TOKEN = "fixture-alpha-tools-only-token-123456789"


def settings():
    return HTTPSettings(
        alpha_url="http://alpha",
        alpha_token=SecretStr(ALPHA_TOKEN),
        mcp_token=SecretStr(TOKEN),
        data_mode="REPLAY",
    )


@pytest.fixture
def app():
    alpha = create_app(
        AlphaSettings(
            _env_file=None,
            alpha_token=SecretStr("fixture-full-service-token-distinct-123456789"),
            alpha_tools_token=SecretStr(ALPHA_TOKEN),
        ),
        calendar=TradingCalendar(),
    )
    return create_http_app(settings(), httpx.ASGITransport(app=alpha))


def test_http_auth_origin_host_and_size_boundaries(app):
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app, base_url="http://127.0.0.1:8200") as client:
        for method in ("GET", "POST", "DELETE"):
            for credential in (None, "wrong", ALPHA_TOKEN):
                headers = {} if credential is None else {"Authorization": f"Bearer {credential}"}
                response = client.request(method, "/mcp", headers=headers)
                assert response.status_code == 401
                assert response.headers["cache-control"] == "no-store"
                assert TOKEN not in response.text and ALPHA_TOKEN not in response.text
        response = client.get("/mcp", headers=[("authorization", f"Bearer {TOKEN}")] * 2)
        assert response.status_code == 401
        for origin in ("https://example.com", "null", "http://127.0.0.1:8200"):
            assert client.get("/mcp", headers={**auth, "Origin": origin}).status_code == 403
        assert client.get("/mcp", headers={**auth, "Host": "evil.example"}).status_code == 421
        response = client.post(
            "/mcp",
            headers={
                **auth,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            content=b" " * 32769,
        )
        assert response.status_code == 413


def test_private_http_mcp_client_reads_real_alpha(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        async def run():
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {TOKEN}"}, trust_env=False
            ) as http:
                transport = streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)
                async with Client(transport, read_timeout_seconds=10) as client:
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


def test_transport_credential_cannot_reuse_alpha_credential():
    with pytest.raises(ValidationError):
        HTTPSettings(
            alpha_url="http://alpha",
            alpha_token=SecretStr(ALPHA_TOKEN),
            mcp_token=SecretStr(ALPHA_TOKEN),
            data_mode="REPLAY",
        )
