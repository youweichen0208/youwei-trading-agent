"""Actual BFF -> Alpha -> OTLP loopback smoke; no database or external collector."""

import argparse
import gzip
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from yta_alpha.api import create_app
from yta_alpha.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collector", choices=["ok", "reject", "stall", "refused"], default="ok")
    mode = parser.parse_args().collector
    payloads = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b";", 1)[0], 16)
                    if not size:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    assert self.rfile.read(2) == b"\r\n"
                body = b"".join(chunks)
            else:
                body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            if "json" in self.headers.get("Content-Type", ""):
                payloads.append(json.loads(body))
            else:
                message = ExportTraceServiceRequest.FromString(body)
                payloads.append(MessageToDict(message))
            if mode == "stall":
                time.sleep(5)
                return
            self.send_response(503 if mode == "reject" else 200)
            self.send_header("Content-Type", self.headers.get("Content-Type", ""))
            self.end_headers()
            if "json" in self.headers.get("Content-Type", ""):
                self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    collector_thread = threading.Thread(target=collector.serve_forever, daemon=True)
    collector_thread.start()
    if mode == "refused":
        collector.shutdown()
        collector.server_close()
    endpoint = f"http://127.0.0.1:{collector.server_port}/v1/traces"
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=None,
        alpha_token="trace-probe-service-token-not-live-12345",
        otlp_traces_url=endpoint,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    alpha_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Alpha probe did not start")
            time.sleep(0.02)
        result = subprocess.run(
            [
                "pnpm",
                "--filter",
                "@yta/bff",
                "exec",
                "tsx",
                "tests/support/tracing-probe.ts",
                alpha_url,
                endpoint,
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            timeout=20,
        )
        assert b"business-response-ok" in result.stdout, "Business request did not succeed"
        output = result.stdout + result.stderr
        assert b"private-probe-marker" not in output
        assert settings.alpha_token.get_secret_value().encode() not in output
        if result.returncode:
            raise RuntimeError("BFF tracing probe shutdown failed")
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        collector.shutdown()
        collector_thread.join(5)
        collector.server_close()
    assert not thread.is_alive(), "Alpha did not terminate within shutdown deadline"
    if mode in ("reject", "stall"):
        services = {
            attribute["value"]["stringValue"]
            for payload in payloads
            for resource in payload["resourceSpans"]
            for attribute in resource["resource"]["attributes"]
            if attribute["key"] == "service.name"
        }
        assert services == {"yta-bff", "yta-alpha"}, (
            "Both exporters must reach the failing collector"
        )
    if mode != "ok":
        print(f"PASS: collector={mode}; business response succeeded and both services stopped")
        return
    spans = [
        span
        for payload in payloads
        for resource in payload["resourceSpans"]
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    ]
    assert len(spans) == 3, f"Expected three spans, got {len(spans)}"

    def identifier(value):
        import base64

        return value.lower() if len(value) in (16, 32) else base64.b64decode(value).hex()

    root = next(span for span in spans if span["name"] == "GET /api/yta/v1/health")
    child = next(span for span in spans if span["name"] == "alpha.request")
    alpha = next(span for span in spans if span["name"] == "GET /internal/v1/health")
    assert len({identifier(span["traceId"]) for span in spans}) == 1
    assert identifier(root["traceId"]) != "a" * 32
    assert identifier(child["parentSpanId"]) == identifier(root["spanId"])
    assert identifier(alpha["parentSpanId"]) == identifier(child["spanId"])
    assert "private-probe-marker" not in json.dumps(payloads)
    assert settings.alpha_token.get_secret_value() not in json.dumps(payloads)
    print(
        "PASS: BFF server -> Alpha client -> Alpha server; "
        "actual OTLP export, private inputs omitted"
    )


if __name__ == "__main__":
    main()
