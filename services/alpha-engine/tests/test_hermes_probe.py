import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("hermes_probe", ROOT / "scripts/probe_hermes.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TOKEN = "probe-fixture-token-not-a-live-secret"


@pytest.fixture
def endpoint():
    state = {"durable": True, "open": False, "redirect": False, "oversize": False, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["calls"].append((self.command, self.path))
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.end_headers()
                return
            if not state["open"] and self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "object": "hermes.api_server.capabilities",
                    "auth": {"type": "bearer", "required": True},
                    "features": {
                        **dict.fromkeys(module.FLAGS, True),
                        "runs_idempotency": {"supported": True, "durable": state["durable"]},
                        "session_continuity_header": "X-Hermes-Session-Id",
                    },
                    "endpoints": module.ENDPOINTS,
                    "padding": "x" * (module.LIMIT if state["oversize"] else 0),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if state.get("slow"):
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_observes_auth_and_claims_without_model_requests_or_runtime_approval(endpoint):
    origin, state = endpoint
    result = module.probe(origin, TOKEN)
    assert result["authentication_statuses"] == [401, 401, 200]
    assert result["required_claims_present"]
    assert not result["runtime_revision_verified"]
    assert result["integration_status"] == "UNVERIFIED"
    assert result["model_requests_sent"] == 0
    assert state["calls"] == [("GET", "/v1/capabilities")] * 3
    assert TOKEN not in json.dumps(result)


def test_memory_only_idempotency_remains_a_failed_required_claim(endpoint):
    origin, state = endpoint
    state["durable"] = False
    result = module.probe(origin, TOKEN)
    assert not result["required_claims_present"]
    assert not result["capability_claims"]["durable_idempotency"]


def test_total_deadline_stops_a_slow_body_that_never_hits_socket_timeout(endpoint, monkeypatch):
    origin, state = endpoint
    state["slow"] = True
    monkeypatch.setattr(module, "DEADLINE_SECONDS", 0.05)
    started = time.monotonic()
    with pytest.raises(ValueError, match="exceeded bounds"):
        module.probe(origin, TOKEN)
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("failure", ["open", "redirect", "oversize"])
def test_rejects_missing_auth_redirects_and_oversized_documents(endpoint, failure):
    origin, state = endpoint
    state[failure] = True
    with pytest.raises(ValueError):
        module.probe(origin, TOKEN)
    assert all(path == "/v1/capabilities" for _method, path in state["calls"])


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "http://127.0.0.1/path",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1?token=secret",
        "file:///tmp/api",
    ],
)
def test_rejects_non_loopback_and_credential_or_path_origins(origin):
    with pytest.raises(ValueError):
        module.probe(origin, TOKEN)
