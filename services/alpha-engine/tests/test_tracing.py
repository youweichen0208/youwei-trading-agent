import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from pydantic import ValidationError
from yta_alpha.api import create_app
from yta_alpha.settings import Settings
from yta_alpha.tracing import AlphaTracing

TOKEN = "trace-test-service-token-not-live-12345"
PRIVATE = "private-query-cookie-prompt"


@pytest.fixture
def collector():
    spans = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = ExportTraceServiceRequest.FromString(body)
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


def test_exports_authenticated_parent_and_ignores_untrusted_context(collector):
    endpoint, spans = collector
    settings = Settings(_env_file=None, alpha_token=TOKEN, otlp_traces_url=endpoint)
    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    headers = {"traceparent": parent, "baggage": PRIVATE, "tracestate": PRIVATE}
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/v1/health?secret=" + PRIVATE,
            headers={
                **headers,
                "Authorization": "Bearer " + TOKEN,
                "Cookie": PRIVATE,
            },
        )
        assert response.status_code == 200
        request_id = response.headers["x-request-id"]
        assert client.get("/internal/v1/health", headers=headers).status_code == 401
        assert client.get("/" + PRIVATE, headers=headers).status_code == 404
    assert len(spans) == 3
    trusted, untrusted, missing = spans
    assert trusted.trace_id.hex() == "a" * 32
    assert trusted.parent_span_id.hex() == "b" * 16
    attrs = {item.key: item.value.string_value for item in trusted.attributes}
    assert attrs["yta.request_id"] == request_id
    assert trusted.name == "GET /internal/v1/health"
    assert untrusted.trace_id != trusted.trace_id and not untrusted.parent_span_id
    assert missing.name == "GET UNMATCHED"
    assert all(not span.events and not span.trace_state for span in spans)
    assert PRIVATE not in str(spans) and TOKEN not in str(spans)


def test_tracing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("YTA_OTLP_TRACES_URL", raising=False)
    tracing = AlphaTracing(Settings(_env_file=None, alpha_token=TOKEN))
    assert tracing.provider is None
    tracing.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:secret@localhost/v1/traces",
        "file:///v1/traces",
        "http://localhost/v1/traces?token=secret",
        "http://localhost/v1/traces#secret",
        "http://localhost/wrong",
    ],
)
def test_rejects_unsafe_export_endpoint(endpoint):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpha_token=TOKEN, otlp_traces_url=endpoint)
