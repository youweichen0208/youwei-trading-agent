import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from mcp import Client
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from pydantic import ValidationError
from yta_alpha.api import create_app
from yta_alpha.settings import Settings as AlphaSettings
from yta_tools.server import Settings, create_server

TOKEN = "fixture-tracing-tools-only-credential-123456789"
PRIVATE = "private-tool-argument-token-marker"


@pytest.fixture
def collector():
    spans = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            message = ExportTraceServiceRequest.FromString(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            for resource in message.resource_spans:
                for scope in resource.scope_spans:
                    spans.extend(scope.spans)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/traces", spans
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def settings(endpoint):
    return Settings(
        alpha_url="http://alpha",
        alpha_token=TOKEN,
        data_mode="REPLAY",
        env="test",
        otlp_traces_url=endpoint,
    )


def test_tool_alpha_trace_and_request_id_correlation(collector):
    endpoint, spans = collector
    alpha = create_app(
        AlphaSettings(
            _env_file=None,
            alpha_token="fixture-main-service-credential-123456789",
            alpha_tools_token=TOKEN,
            otlp_traces_url=endpoint,
        )
    )

    async def run():
        async with alpha.router.lifespan_context(alpha):
            async with Client(
                create_server(settings(endpoint), httpx.ASGITransport(app=alpha))
            ) as c:
                result = await c.call_tool("market_snapshot", {})
                assert not result.is_error
                assert result.structured_content["snapshot"]["data_mode"] == "REPLAY"
                assert (await c.call_tool(PRIVATE, {})).is_error
                assert (await c.call_tool("indicators", {"secret": PRIVATE})).is_error

    asyncio.run(run())
    assert len(spans) == 5
    root = next(span for span in spans if span.name == "tool.market_snapshot")
    child = next(span for span in spans if span.name == "alpha.request")
    alpha = next(span for span in spans if span.name.startswith("GET /internal/"))
    assert not root.parent_span_id
    assert root.trace_id == child.trace_id == alpha.trace_id
    assert child.parent_span_id == root.span_id
    assert alpha.parent_span_id == child.span_id
    child_attrs = {item.key: item.value.string_value for item in child.attributes}
    alpha_attrs = {item.key: item.value.string_value for item in alpha.attributes}
    assert child_attrs["yta.upstream_request_id"] == alpha_attrs["yta.request_id"]
    errors = [span for span in spans if span.status.code == 2]
    assert len(errors) == 2
    assert {span.name for span in errors} == {"tool.UNKNOWN_TOOL", "tool.indicators"}
    assert all(not span.events for span in spans)
    assert PRIVATE not in str(spans) and TOKEN not in str(spans)


def test_tool_upstream_failure_has_safe_code_and_no_exception_content(collector):
    endpoint, spans = collector

    def handler(request):
        assert "traceparent" in request.headers
        assert "baggage" not in request.headers and "tracestate" not in request.headers
        return httpx.Response(503, text=PRIVATE, headers={"x-request-id": PRIVATE})

    async def run():
        async with Client(
            create_server(settings(endpoint), httpx.MockTransport(handler))
        ) as client:
            result = await client.call_tool("indicators", {})
            assert result.is_error and result.content[0].text == "DATA_UNAVAILABLE"

    asyncio.run(run())
    assert len(spans) == 2
    assert all(span.status.code == 2 and not span.events for span in spans)
    assert PRIVATE not in str(spans) and TOKEN not in str(spans)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:secret@localhost/v1/traces",
        "file:///v1/traces",
        "http://localhost/v1/traces?token=secret",
        "http://localhost/wrong",
    ],
)
def test_tool_tracing_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(ValidationError):
        settings(endpoint)


def test_cancelled_tool_ends_spans_without_exception_text(collector):
    endpoint, spans = collector

    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def handler(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with Client(
            create_server(settings(endpoint), httpx.MockTransport(handler))
        ) as client:
            task = asyncio.create_task(client.call_tool("indicators", {}))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 3)

    asyncio.run(run())
    assert len(spans) == 2
    assert all(span.status.code == 2 and not span.events for span in spans)
    assert {span.name for span in spans} == {"tool.indicators", "alpha.request"}


def test_private_http_transport_flushes_only_authorized_tool_spans(collector):
    from starlette.testclient import TestClient
    from yta_tools.http import HTTPSettings, create_http_app

    endpoint, spans = collector
    token = "fixture-mcp-transport-private-token-123456789"
    config = HTTPSettings(**settings(endpoint).model_dump(), mcp_token=token)
    app = create_http_app(config, httpx.MockTransport(lambda _: httpx.Response(503, text=PRIVATE)))
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "indicators", "arguments": {}},
    }
    with TestClient(app, base_url="http://127.0.0.1:8200") as client:
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        response = client.post(
            "/mcp",
            json=body,
            headers={
                **headers,
                "Authorization": "Bearer " + token,
                "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
                "baggage": PRIVATE,
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is True
    assert len(spans) == 2
    assert all(span.trace_id.hex() != "a" * 32 for span in spans)
    assert PRIVATE not in str(spans) and token not in str(spans)
