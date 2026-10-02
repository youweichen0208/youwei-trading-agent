#!/usr/bin/env python3
"""Bridge-scope probe: can the tool_search `tool_call` meta-tool escape the
session toolset?

Context (S07 real-gateway verification): Hermes collapses deferred plugin
tools behind tool_search/tool_describe/tool_call. The research agent's only
enabled toolset is `youwei-research` (snapshot_manifest), so the model sees
the 3 bridge tools instead. This probe proves empirically, through the REAL
agent machinery (no real LLM — an in-process scripted mock gateway):

  1. tool_call(calls=[{name: "terminal", ...}])      -> BLOCKED by session
     scope ("not available in this session"), never executed
  2. tool_call(calls=[{name: "snapshot_manifest"}])  -> dispatched to the
     platform handler (frozen-evidence manifest returned, authorization via
     the contextvar re-check intact)

Runs INSIDE the agent-runtime image (needs the pinned Hermes checkout on
sys.path + youwei_agent_runtime):

  docker run --rm --network none \
      -v <checkout>/services/agent-runtime/smoke:/smoke:ro \
      --entrypoint python3 \
      ghcr.io/youweichen0208/youwei-agent-runtime@sha256:998f060e... \
      /smoke/bridge_scope_probe.py

Exit 0 = scope enforced (blocked) AND platform dispatch works.
"""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys

sys.path.insert(0, "/opt/hermes")

from youwei_agent_runtime.adapter import build_research_brief  # noqa: E402
from youwei_agent_runtime.runtime import (  # noqa: E402
    ResearchConfig,
    _register_research_tools,
    make_agent,
)
from youwei_agent_runtime.tools import ToolContext, set_tool_context, reset_tool_context  # noqa: E402

from youwei_contracts.research import FrozenEvidence  # noqa: E402
from youwei_contracts.research_capability import (  # noqa: E402
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
)

import hashlib  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

PORT = 9953
_requests = []  # per gateway request: tools list + message roles
tool_results = []  # role=tool message contents observed in incoming request bodies


def _sse_tool_call(call_id: str, name: str, args: dict) -> bytes:
    delta = {
        "role": "assistant",
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }
    obj = {"id": "chatcmpl-probe", "object": "chat.completion.chunk", "created": 0,
           "model": "probe", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    return f"data: {json.dumps(obj)}\n\n".encode()


def _sse_content(content: str, finish: str | None = None) -> bytes:
    obj = {"id": "chatcmpl-probe", "object": "chat.completion.chunk", "created": 0,
           "model": "probe",
           "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]}
    return f"data: {json.dumps(obj)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    """Scripted model. The agent may emit an auxiliary/priming request with NO
    tools first (observed in the real Hermes facade); script only tool-bearing
    requests: #1 tries `terminal` via the bridge, #2 calls snapshot_manifest,
    #3 answers. Tool results are captured from the NEXT request's messages
    (the agent feeds tool results back), so the agent's internal transcript
    structure does not matter."""

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        messages = body.get("messages", [])
        tools = [t["function"]["name"] for t in body.get("tools", [])]
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "tool":
                tool_results.append(str(m.get("content", "")))
        _requests.append({"tools": tools,
                          "roles": [m.get("role") for m in messages if isinstance(m, dict)]})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        tool_bearing_n = sum(1 for r in _requests if r["tools"])
        if tool_bearing_n == 1:
            # escape attempt 1: invoke a non-enabled core tool THROUGH the bridge
            self.wfile.write(_sse_tool_call(
                "call-terminal", "tool_call",
                {"calls": [{"name": "terminal",
                            "arguments": {"command": "id > /tmp/pwned"}}]}))
            self.wfile.write(_sse_content("", finish="tool_calls"))
        elif tool_bearing_n == 2:
            # legit path: platform tool through the bridge
            self.wfile.write(_sse_tool_call(
                "call-manifest", "tool_call",
                {"calls": [{"name": "snapshot_manifest", "arguments": {}}]}))
            self.wfile.write(_sse_content("", finish="tool_calls"))
        elif tool_bearing_n == 3:
            # escape attempt 2: DIRECT tool call to a non-enabled core tool
            # (name not in the tools list the gateway was given)
            self.wfile.write(_sse_tool_call(
                "call-terminal-direct", "terminal",
                {"command": "id > /tmp/pwned-direct"}))
            self.wfile.write(_sse_content("", finish="tool_calls"))
        else:
            self.wfile.write(_sse_content("probe complete"))
            self.wfile.write(_sse_content("", finish="stop"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


def make_probe_evidence() -> FrozenEvidence:
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": f"2026-09-{20 + i:02d}",
             "close": 100.0 + i, "volume": 1000,
             "provenance": {"raw_object_id": str(uuid.uuid4())}} for i in range(3)]
    sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return FrozenEvidence(
        run_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        case={"case_id": str(uuid.uuid4()), "security_id": sec,
              "benchmark_security_id": str(uuid.uuid4()), "horizon_td": 20,
              "target_spec_id": "target-spec-v1", "target_spec_sha256": "c" * 64,
              "decision_cutoff_utc": "2026-09-26T10:00:00+00:00",
              "prediction_deadline_utc": "2026-09-28T13:15:00+00:00",
              "entry_at_utc": "2026-09-28T13:30:00+00:00",
              "exit_at_utc": "2026-10-23T20:00:00+00:00"},
        evidence={"snapshot_id": str(uuid.uuid4()), "kind": "daily_bars",
                  "as_of": "2026-09-26T10:00:00+00:00", "mode": "forward",
                  "content_sha256": sha, "content": bars,
                  "manifest": {"code_version": "daily-bars-snapshot-v1"}},
        target_policy_sha256="d" * 64,
        batch_manifest={"calendar_version": "nyse-rules-v1"})


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _register_research_tools()
    evidence = make_probe_evidence()

    priv, pub = generate_research_keypair()
    kid = public_key_thumbprint(pub)[:16]
    token = sign_research_token(
        priv, kid=kid, aud=AUD_RUNTIME_RESEARCH,
        invocation_id=uuid.uuid4(), tenant_id=evidence.tenant_id,
        run_id=evidence.run_id, job_id=uuid.uuid4(), attempt_no=1,
        case_id=evidence.case.case_id,
        evidence_sha256="0" * 64, exec_config_version="probe-v1",
        scopes=(SCOPE_RESEARCH_RUN,),
        exp=datetime.now(UTC) + timedelta(minutes=5))

    agent = make_agent(ResearchConfig(
        base_url=f"http://127.0.0.1:{PORT}/v1", api_key="probe-key",
        model="probe", max_iterations=8))
    print("agent tools:", sorted(agent.valid_tool_names))

    ctx = ToolContext(evidence=evidence, capability_token=token, public_keys={kid: pub})
    ctx_token = set_tool_context(ctx)
    try:
        answer = agent.chat(build_research_brief(evidence))
    finally:
        reset_tool_context(ctx_token)
    server.shutdown()

    print("final answer:", str(answer)[:120])
    print("gateway requests:", json.dumps(_requests))
    unique_results = list(dict.fromkeys(tool_results))
    print(f"tool results fed back: {len(tool_results)} (unique: {len(unique_results)})")
    for i, content in enumerate(unique_results):
        print(f"  result[{i}]: {content[:200]}")

    blocked = dispatched = executed_terminal = False
    for content in unique_results:
        if ("not available in this session" in content
                or "not a deferred one" in content
                or "not a valid tool" in content.lower()
                or "unknown tool" in content.lower()
                or "does not exist" in content.lower()):
            blocked = True
            print(f"tool result (blocked): {content[:160]}")
        elif "as_of" in content and "content_sha256" in content:
            dispatched = True
            print(f"tool result (platform manifest): {content[:140]}")
        elif "pwned" in content or "uid=" in content:
            executed_terminal = True
            print(f"DANGER tool result (terminal executed!): {content[:200]}")
    if executed_terminal:
        print("BRIDGE SCOPE FAILED: terminal executed through the bridge")
        return 1
    if blocked and dispatched:
        print("BRIDGE SCOPE OK: out-of-toolset call blocked, platform tool dispatched")
        return 0
    print(f"BRIDGE SCOPE INCONCLUSIVE blocked={blocked} dispatched={dispatched} "
          "(inspect transcript above)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
