"""Local OpenAI-compatible SSE streaming mock for the no-cost smoke test.

Hermes ``chat()`` uses streaming (``stream=true`` + ``stream_options``), so the
mock must return an SSE (``text/event-stream``) response, not a plain JSON
object. Run this on the SG host (where the pinned Hermes checkout and Python
3.14 venv live), then drive ``smoke_e2e.py`` against it. No real model, no cost.
"""

import json
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = "/tmp/mock_gateway_requests.log"

PROPOSAL = {
    "source_status": "produced",
    "p_outperform": 0.6,
    "expected_excess_return": 0.02,
    "references": [],  # filled from the actual request's frozen row locator
    "warnings": [],
    "missing": [],
    "quantitative_basis": "mock smoke test",
    "quant_relation": "kept",
    "model": {"model_version": "mock-v0", "provider": "mock"},
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else {}
        locator = re.search(
            r"snapshot:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/rows/(?:0|[1-9][0-9]*)",
            json.dumps(body.get("messages", [])),
        )
        if locator is None:
            self.send_error(400, "smoke request must provide a frozen row locator")
            return
        proposal = dict(PROPOSAL)
        proposal["references"] = [{"kind": "evidence", "locator": locator.group()}]
        content = json.dumps(proposal)
        with open(LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "path": self.path,
                        "stream": body.get("stream"),
                        "keys": list(body.keys()),
                        "model": body.get("model"),
                    }
                )
                + "\n"
            )

        model = body.get("model", "mock")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.close_connection = True
        self.end_headers()

        def chunk(delta_content=None, role=None, finish=None, usage=None):
            obj = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
            if role:
                obj["choices"][0]["delta"]["role"] = role
            if delta_content:
                obj["choices"][0]["delta"]["content"] = delta_content
            if usage:
                obj["usage"] = usage
            return f"data: {json.dumps(obj)}\n\n"

        # role chunk, then content split into pieces, then finish + usage
        self.wfile.write(chunk(role="assistant").encode())
        self.wfile.write(chunk(delta_content=content[: len(content) // 2]).encode())
        self.wfile.flush()
        time.sleep(0.05)
        self.wfile.write(chunk(delta_content=content[len(content) // 2 :]).encode())
        self.wfile.write(
            chunk(
                finish="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ).encode()
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9901
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    HTTPServer((host, port), Handler).serve_forever()
