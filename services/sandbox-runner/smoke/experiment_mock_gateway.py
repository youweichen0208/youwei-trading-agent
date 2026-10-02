#!/usr/bin/env python3
"""Scripted SSE gateway for the S08 mock acceptance (zero cost, no real LLM).

One process serves BOTH roles, discriminated by the brief in the first user
message (the request bodies are the ONLY input; the gateway never sees env):

- RESEARCH turn 1 (brief contains "Research brief" and NO earlier-turn
  experiments) -> answers with an EXPERIMENT REQUEST JSON (question /
  motivation / requested_shape).
- RESEARCH re-entry turn (brief contains "Experiment outcomes from earlier
  turns") -> answers with a PROPOSAL JSON citing the experiment artifact
  whose locator appears in the brief (kind "code").
- EXPERIMENT turn (brief contains "Experiment brief") -> drives the tool
  loop like a model would: sandbox_submit, then sandbox_status until the
  computation is terminal (each poll is a REAL call to the Runner through
  the container's tool plane), then artifact_read, then the final findings
  JSON.

Hermes may send an auxiliary/priming request with no tools (observed in the
real gateway verification); those get a plain content answer and do not
advance the script. Tool results are read from the incoming request's
role=tool messages (the agent feeds them back), so the script reacts to
what the tools ACTUALLY returned.

Run (inside the agent-runtime image, attached to BOTH the research and the
experiment networks):
  docker run -d --name mock-gateway --network youwei-research \
      -v <checkout>/services/sandbox-runner/smoke:/smoke:ro \
      --entrypoint python3 youwei/agent-runtime:s08c \
      /smoke/experiment_mock_gateway.py 9901 0.0.0.0
  docker network connect --alias mock-gateway youwei-experiment mock-gateway
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9901
BIND = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"

LOG = "/tmp/mock_gateway_requests.log"

EXPERIMENT_CODE = (
    "import json\n"
    "rows = json.load(open('/inputs/snapshot/content.json'))\n"
    "closes = [r['close'] for r in rows]\n"
    "ratio = (max(closes) - min(closes)) / min(closes)\n"
    "open('/outputs/ratio.json', 'w').write(json.dumps({'ratio': round(ratio, 6)}))\n"
    "print('computed ratio over', len(rows), 'rows')\n"
)

REQUESTS = []  # per request: role + tool activity, for the acceptance record


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def _sse_tool_call(call_id: str, name: str, args: dict) -> bytes:
    delta = {
        "role": "assistant",
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }
    obj = {"id": "chatcmpl-s08", "object": "chat.completion.chunk", "created": 0,
           "model": "mock", "choices": [{"index": 0, "delta": delta,
                                         "finish_reason": None}]}
    return _sse(obj)


def _sse_content(content: str, finish: str | None = None) -> bytes:
    obj = {"id": "chatcmpl-s08", "object": "chat.completion.chunk", "created": 0,
           "model": "mock",
           "choices": [{"index": 0, "delta": {"content": content},
                        "finish_reason": finish}]}
    return _sse(obj)


def _bridge_call(call_id: str, name: str, args: dict) -> bytes:
    """Emit a tool call THROUGH the Tool Search bridge (tool_call meta-tool).

    Hermes collapses plugin tools behind tool_search/tool_describe/tool_call;
    a direct name (e.g. "sandbox_submit") is rejected by session scope (the
    S07n bridge-scope probe pinned this). The bridge form is the only path
    the model actually has."""
    return _sse_tool_call(call_id, "tool_call", {
        "calls": [{"name": name, "arguments": args}],
    })


def _last_tool_result(messages) -> dict | None:
    """The most recent role=tool message content, parsed as JSON. The Tool
    Search bridge may wrap the inner tool's output; dig one level in."""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "tool":
            content = message.get("content", "")
            parsed = _parse_json_ish(content)
            if isinstance(parsed, dict):
                # bridge envelope: {"results": [...]} or {"output": "..."}
                for key in ("results", "outputs"):
                    inner = parsed.get(key)
                    if isinstance(inner, list) and inner:
                        first = inner[0]
                        dug = _parse_json_ish(
                            first.get("content", first)
                            if isinstance(first, dict) else first
                        )
                        if isinstance(dug, dict):
                            return dug
                if isinstance(parsed.get("output"), str):
                    dug = _parse_json_ish(parsed["output"])
                    if isinstance(dug, dict):
                        return dug
                return parsed
            return {"raw": str(content)[:400]}
    return None


def _parse_json_ish(text):
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (TypeError, ValueError):
                return None
    return None


def _brief_text(messages) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        messages = body.get("messages", [])
        tools = [t["function"]["name"] for t in body.get("tools", [])]
        brief = _brief_text(messages)
        tool_result = _last_tool_result(messages)
        REQUESTS.append({
            "tools": tools,
            "brief_head": brief[:40],
            "last_tool_result": tool_result,
        })
        with open(LOG, "a") as fh:
            fh.write(json.dumps(REQUESTS[-1], sort_keys=True) + "\n")

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        if not tools:
            # auxiliary/priming request: plain answer, does not advance the script
            self.wfile.write(_sse_content("ok"))
            self.wfile.write(_sse_content("", finish="stop"))
        elif "Experiment brief" in brief:
            self._experiment_turn(self.wfile, tool_result)
        elif "Experiment outcomes from earlier turns" in brief:
            self._research_reentry(self.wfile, brief)
        else:
            self._research_first_turn(self.wfile)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # --- the experiment instance's model --------------------------------

    def _experiment_turn(self, out, tool_result):
        if tool_result is None:
            # fresh turn: submit the computation (through the bridge)
            out.write(_bridge_call("call-submit", "sandbox_submit", {
                "code": EXPERIMENT_CODE,
                "expected_extensions": [".json"],
                "timeout_seconds": 60.0,
            }))
            out.write(_sse_content("", finish="tool_calls"))
            return
        if tool_result.get("error") or "status_code" in tool_result:
            # tool-plane error surfaced to the model: stop and report honestly
            # (a status object's always-present "error": null is NOT an error)
            out.write(_sse_content(json.dumps({
                "findings": f"experiment tool error: {tool_result.get('error')}",
                "warnings": ["tool error"],
            })))
            out.write(_sse_content("", finish="stop"))
            return
        status = tool_result.get("status")
        computation_id = tool_result.get("computation_id")
        artifacts = tool_result.get("artifacts") or []
        if status == "running":
            # a real model thinks between polls; without this the guardrail
            # (identical_call_streak_halt) stops the loop before the sandbox
            # computation (observed ~2s) reaches a terminal state
            import time

            time.sleep(1.0)
            out.write(_bridge_call("call-status", "sandbox_status", {
                "computation_id": computation_id,
            }))
            out.write(_sse_content("", finish="tool_calls"))
        elif status in ("succeeded", "failed", "timeout", "cancelled") and not any(
            "content" in (a or {}) for a in artifacts
        ):
            # terminal with artifacts listed but not yet read
            if artifacts:
                out.write(_bridge_call("call-read", "artifact_read", {
                    "computation_id": computation_id,
                    "path": artifacts[0]["path"],
                }))
                out.write(_sse_content("", finish="tool_calls"))
            else:
                out.write(_sse_content(json.dumps({
                    "findings": f"computation {status} with no artifacts",
                    "warnings": [],
                })))
                out.write(_sse_content("", finish="stop"))
        else:
            # the artifact content came back: final findings citing it
            content = tool_result.get("content", "")
            out.write(_sse_content(json.dumps({
                "findings": (
                    f"the injected snapshot yields {content.strip()}; the "
                    f"computation {computation_id} produced the cited artifact"
                ),
                "warnings": [],
            })))
            out.write(_sse_content("", finish="stop"))

    # --- the research instance's model -----------------------------------

    def _research_first_turn(self, out):
        out.write(_sse_content(json.dumps({
            "question": (
                "20-session rolling realized volatility ratio of the security "
                "vs the benchmark, and the current ratio's quantile in its "
                "own history"
            ),
            "motivation": (
                "the frozen evidence shows the bars but the statistic is not "
                "covered by the available tools; a computation is needed"
            ),
            "requested_shape": "{ratio: float, quantile: float}",
        })))
        out.write(_sse_content("", finish="stop"))

    def _research_reentry(self, out, brief: str):
        # cite the experiment artifact whose locator the brief rendered
        match = re.search(r"experiment:[0-9a-f-]+/computations/[0-9a-f-]+/artifacts/\S+", brief)
        locator = match.group(0).split()[0] if match else "experiment:missing"
        proposal = {
            "source_status": "produced",
            "p_outperform": 0.6,
            "expected_excess_return": 0.02,
            "references": [
                {"kind": "code", "locator": locator, "note": "the vol-ratio artifact"},
            ],
            "warnings": [],
            "missing": [],
            "quantitative_basis": "experiment outcome cited as the quantitative basis",
            "quant_relation": "kept",
        }
        out.write(_sse_content(json.dumps(proposal)))
        out.write(_sse_content("", finish="stop"))

    def log_message(self, fmt, *args):
        pass


def main() -> int:
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"experiment mock gateway on {BIND}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
