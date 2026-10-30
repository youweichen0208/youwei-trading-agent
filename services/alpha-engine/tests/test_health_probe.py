import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("check_health", ROOT / "scripts/check_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


@pytest.mark.parametrize(
    "status,body,mode,expected",
    [
        (200, {"status": "ready", "service": "yta-bff", "data_mode": "REPLAY"}, "REPLAY", "ready"),
        (
            200,
            {"status": "ready", "service": "yta-bff", "data_mode": "REPLAY"},
            "LIVE_RESEARCH",
            "not_ready",
        ),
        (503, {"status": "not_ready", "secret": "must-not-leak"}, "REPLAY", "not_ready"),
        (200, "invalid-json-must-not-leak", "REPLAY", "not_ready"),
        (200, "x" * 9000, "REPLAY", "not_ready"),
        (
            200,
            {"status": "ready", "service": "other", "data_mode": "REPLAY"},
            "REPLAY",
            "not_ready",
        ),
    ],
)
def test_monitor_checks_status_mode_and_limits_without_echoing_response(
    status, body, mode, expected
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write((body if isinstance(body, str) else json.dumps(body)).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = health.probe(f"http://127.0.0.1:{server.server_port}/ready", mode)
        assert result["status"] == expected
        assert "must-not-leak" not in str(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_monitor_does_not_follow_redirect():
    visited = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = health.probe(f"http://127.0.0.1:{server.server_port}/ready", "REPLAY")
        assert result["status"] == "not_ready"
        assert visited == ["/ready"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
