"""Synthetic OpenAI-compatible protocol fixture. Never use as a research provider."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "yta-protocol-fixture"
TOOLS = ("mcp__yta_finance__market_snapshot", "mcp__yta_finance__indicators")
TEXT = "YTA synthetic protocol fixture completed. This is not live research or model analysis."


def answer(body):
    if body.get("model") != MODEL or not isinstance(body.get("messages"), list):
        raise ValueError("Unsupported fixture request")
    advertised = {tool.get("function", {}).get("name") for tool in body.get("tools", [])}
    completed = {
        message.get("tool_call_id") for message in body["messages"] if message.get("role") == "tool"
    }
    done = {
        call.get("function", {}).get("name")
        for message in body["messages"]
        for call in message.get("tool_calls", [])
        if call.get("id") in completed
    }
    if set(TOOLS) <= advertised:
        for index, name in enumerate(TOOLS):
            if name not in done:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_yta_fixture_{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": "{}"},
                        }
                    ],
                }, "tool_calls"
    return {"role": "assistant", "content": TEXT}, "stop"


def main():
    if os.environ.get("YTA_MODEL_FIXTURE_MODE") != "REPLAY":
        raise SystemExit("Fixture requires explicit REPLAY mode")
    token = os.environ["YTA_MODEL_FIXTURE_TOKEN"]
    lock = threading.Lock()
    observations = {
        "requests": 0,
        "tool_calls": [],
        "models": [],
        "advertised_tools": [],
        "synthetic": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            return self.headers.get("Authorization") == "Bearer " + token

        def do_GET(self):
            if self.path == "/health":
                return self.send_json(200, {"synthetic": True, "mode": "REPLAY"})
            if self.path == "/stats" and self.authorized():
                with lock:
                    return self.send_json(200, observations)
            self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            self.connection.settimeout(5)
            if not self.authorized():
                return self.send_json(401, {"error": "unauthorized"})
            if self.path != "/v1/chat/completions":
                return self.send_json(404, {"error": "unsupported_fixture_endpoint"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("Invalid length")
                body = json.loads(self.rfile.read(length))
                message, finish = answer(body)
            except (ValueError, TypeError, AttributeError):
                return self.send_json(400, {"error": "invalid_fixture_request"})
            with lock:
                observations["requests"] += 1
                observations["models"].append(body["model"])
                observations["advertised_tools"].append(
                    [tool.get("function", {}).get("name") for tool in body.get("tools", [])]
                )
                observations["tool_calls"].extend(
                    call["function"]["name"] for call in message.get("tool_calls", [])
                )
                number = observations["requests"]
            common = {"id": f"chatcmpl-yta-fixture-{number}", "created": 1, "model": MODEL}
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            if not body.get("stream"):
                return self.send_json(
                    200,
                    {
                        **common,
                        "object": "chat.completion",
                        "usage": usage,
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    },
                )
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"] = [
                    {"index": index, **call} for index, call in enumerate(delta["tool_calls"])
                ]
            chunks = [
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": usage,
                },
            ]
            payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            encoded = (payload + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    ThreadingHTTPServer(("0.0.0.0", 8300), Handler).serve_forever()


if __name__ == "__main__":
    main()
