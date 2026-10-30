"""Feed adversarial tool calls through the locked real agent loop, entirely on loopback."""

import contextlib
import io
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    if os.environ.get("YTA_TOOL_EXECUTION_PROBE") != "1":
        raise RuntimeError("Explicit isolated probe required")
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    assert active == {"lo"}
    home = Path("/tmp/yta-tool-policy")
    home.mkdir(mode=0o700)
    os.environ["HERMES_HOME"] = str(home)
    (home / "config.yaml").write_text(
        json.dumps(
            {
                "tools": {"tool_search": {"enabled": "off"}},
                "mcp_servers": {},
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
                "agent": {"disabled_toolsets": ["x_search"]},
            }
        )
    )
    marker = home / "forbidden-marker"
    forbidden = {
        "terminal": {"command": "touch " + str(marker)},
        "write_file": {"path": str(marker), "content": "unexpected"},
        "read_file": {"path": str(home / "config.yaml")},
        "execute_code": {"code": "open(" + repr(str(marker)) + ", 'w').write('unexpected')"},
        "delegate_task": {"task": "Write a marker in the temporary directory"},
        "tool_call": {
            "name": "write_file",
            "arguments": {"path": str(marker), "content": "unexpected"},
        },
    }
    observed = {"advertised": [], "results": {}, "model_requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed["model_requests"] += 1
            names = [tool["function"]["name"] for tool in body.get("tools", [])]
            if names:
                observed["advertised"].append(names)
            results = {
                message.get("name", message.get("tool_call_id")): message.get("content")
                for message in body["messages"]
                if message.get("role") == "tool"
            }
            if results:
                observed["results"].update(results)
            if names and not results:
                calls = {
                    "todo_list": {
                        "todos": [
                            {"id": "allowed", "content": "authorized probe", "status": "completed"}
                        ]
                    },
                    **forbidden,
                }
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_" + name,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for name, args in calls.items()
                    ],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "YTA_TOOL_POLICY_OK"}
                finish = "stop"
            common = {"id": "chatcmpl-policy", "created": 1, "model": "yta-policy-fixture"}
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            if body.get("stream"):
                delta = dict(message)
                if "tool_calls" in delta:
                    delta["tool_calls"] = [
                        {"index": i, **call} for i, call in enumerate(delta["tool_calls"])
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
                encoded = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                ).encode()
                content_type = "text/event-stream"
            else:
                encoded = json.dumps(
                    {
                        **common,
                        "object": "chat.completion",
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": usage,
                    }
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started, completed = [], []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            from run_agent import AIAgent

            agent = AIAgent(
                model="yta-policy-fixture",
                provider="custom",
                api_mode="chat_completions",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="synthetic-probe-token",
                enabled_toolsets=["todo", "no_mcp"],
                disabled_toolsets=["x_search"],
                max_iterations=3,
                max_tokens=128,
                reasoning_config={"enabled": False},
                quiet_mode=True,
                verbose_logging=False,
                skip_context_files=True,
                tool_start_callback=lambda call_id, name, args: started.append(name),
                tool_complete_callback=lambda call_id, name, args, result: completed.append(name),
            )
            result = agent.run_conversation(
                "Controlled tool permission probe. Only use allowed tools."
            )
        assert (
            result.get("final_response") == "YTA_TOOL_POLICY_OK" and result.get("completed") is True
        )
        assert observed["advertised"] and all(
            names == ["todo_list"] for names in observed["advertised"]
        )
        assert started == completed == ["todo_list"]
        for name in forbidden:
            content = observed["results"].get(name, observed["results"].get("call_" + name, ""))
            assert "does not exist" in content, "Forbidden call lacked an explicit rejection"
        allowed = observed["results"].get(
            "todo_list", observed["results"].get("call_todo_list", "")
        )
        assert "authorized probe" in allowed
        assert not marker.exists()
        print(
            json.dumps(
                {
                    "actual_agent_loop": True,
                    "synthetic_adversarial_model": True,
                    "advertised_tools": ["todo_list"],
                    "executed_tools": started,
                    "forbidden_tools_rejected": list(forbidden),
                    "marker_absent": True,
                    "external_model_requests": 0,
                    "network": "none",
                    "qualification_status": "UNVERIFIED",
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            json.dumps({"error": "TOOL_EXECUTION_PROBE_FAILED", "type": type(error).__name__})
        ) from None
