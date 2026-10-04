#!/usr/bin/env python3
"""S07 gateway-level verification matrix against the deployed LiteLLM.

Runs on sg-prod (needs the gateway on 127.0.0.1:4000). Stdlib only. These
tests verify GATEWAY behavior (the transport the research chain depends on);
the chain-level end-to-end is research_real_gateway.py.

Subcommands (each prints a compact PASS/FAIL/OBSERVED verdict):

  models        GET /v1/models with the research key (visibility check)
  toolcall      streaming chat completion WITH a tool definition; reconstruct
                tool_calls from SSE deltas, then send the tool result back and
                get the final answer (full OpenAI-wire tool round-trip).
                --no-stream variant for diagnosis.
  cancel        open a streaming completion, read a few SSE chunks, then abort
                the client connection; print a timestamp marker for gateway
                log / spend correlation.
  concurrency   with the LIMITS key (max_parallel_requests=1): one long
                streaming request + one overlapping request; the second must
                be rejected (429) while the first is in flight.
  rate          with the LIMITS key (rpm_limit=2): three tiny sequential
                requests; the third must be rejected (429) within the window.
  tpm           with the LIMITS key (tpm_limit set low): one large-prompt
                request passes (prior spend 0), the next is rejected (429).
  spend         GET /spend/logs (master key); print recent rows for usage /
                aux-call correlation. Prints tokens only, never keys.

Environment:
  GW_BASE       default http://127.0.0.1:4000
  GW_KEY        REQUIRED (except `spend`): the research virtual key.
  GW_LIMITS_KEY REQUIRED for concurrency/rate/tpm: the limits test key.
  GW_MODEL      default glm-5.3.
  MASTER_KEY    REQUIRED for `spend`: the LiteLLM master key (server-side).
"""

import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "snapshot_manifest",
        "description": (
            "Report the frozen evidence snapshot manifest for the current "
            "research run: snapshot id, kind, as_of, mode, content hash, row "
            "count, coverage per security, and referenced source versions."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def base_parts():
    base = env("GW_BASE", "http://127.0.0.1:4000")
    u = urllib.parse.urlsplit(base)
    return u.hostname or "127.0.0.1", u.port or 80


def connect(timeout: float = 60.0) -> http.client.HTTPConnection:
    host, port = base_parts()
    return http.client.HTTPConnection(host, port, timeout=timeout)


def post_json(path: str, key: str, body: dict, timeout: float = 60.0):
    """Non-streaming POST; returns (status, parsed_json_or_text)."""
    conn = connect(timeout)
    try:
        conn.request(
            "POST", path, body=json.dumps(body),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw
    finally:
        conn.close()


def open_stream(body: dict, key: str, timeout: float = 120.0):
    """Open a streaming completion; returns (conn, resp). Caller owns conn."""
    conn = connect(timeout)
    payload = dict(body)
    payload["stream"] = True
    conn.request(
        "POST", "/v1/chat/completions", body=json.dumps(payload),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    resp = conn.getresponse()
    return conn, resp


def iter_sse(resp):
    """Yield parsed SSE data payloads (dicts) from an open streaming response."""
    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                return
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def drain_stream(resp, on_chunk=None, max_chunks=None):
    """Read a whole stream; returns (content, tool_calls, chunks, finish)."""
    content_parts = []
    tool_calls = {}  # index -> {"id":..., "name":..., "args": str}
    finish = None
    chunks = 0
    for payload in iter_sse(resp):
        chunks += 1
        if on_chunk:
            on_chunk(payload)
        for choice in payload.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                acc = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
        if max_chunks is not None and chunks >= max_chunks:
            break
    return "".join(content_parts), list(tool_calls.values()), chunks, finish


def cmd_models():
    key = env("GW_KEY")
    conn = connect()
    try:
        conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {key}"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        status = resp.status
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
    finally:
        conn.close()
    if status == 200 and isinstance(body, dict):
        ids = [m.get("id") for m in body.get("data", [])]
        print(f"models: status=200 count={len(ids)} ids={ids}")
        return 0
    print(f"models: FAIL status={status} body={str(body)[:300]}")
    return 1


def cmd_toolcall(no_stream: bool = False):
    key = env("GW_KEY")
    model = env("GW_MODEL", "glm-5.3")
    messages = [
        {
            "role": "user",
            "content": (
                "Call the snapshot_manifest tool now. After you receive its "
                "result, reply with the exact snapshot_id value from the "
                "manifest and nothing else."
            ),
        }
    ]
    body = {
        "model": model,
        "messages": messages,
        "tools": [TOOL_DEF],
        "max_tokens": 512,
    }
    if no_stream:
        status, payload = post_json("/v1/chat/completions", key, body)
        print(f"toolcall[nostream]: status={status}")
        if status != 200:
            print(f"  body={str(payload)[:300]}")
            return 1
        msg = payload["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        print(f"  finish={payload['choices'][0].get('finish_reason')} "
              f"tool_calls={[t['function']['name'] for t in tcs]}")
        return 0 if tcs else 1

    conn, resp = open_stream(body, key)
    try:
        if resp.status != 200:
            print(f"toolcall: FAIL status={resp.status} body={resp.read()[:300]!r}")
            return 1
        content, tool_calls, chunks, finish = drain_stream(resp)
    finally:
        conn.close()
    print(f"toolcall[stream]: chunks={chunks} finish={finish}")
    print(f"  tool_calls={json.dumps(tool_calls)}")
    print(f"  content={content[:200]!r}")
    if not tool_calls:
        print("toolcall: OBSERVED no tool call in stream (model declined or "
              "translation gap) — see nostream variant for diagnosis")
        return 1
    # Round-trip: send the tool result back.
    tc0 = tool_calls[0]
    messages.append({
        "role": "assistant",
        "content": content or None,
        "tool_calls": [{
            "id": tc0["id"] or "call_0",
            "type": "function",
            "function": {"name": tc0["name"] or "snapshot_manifest",
                         "arguments": tc0["args"] or "{}"},
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": tc0["id"] or "call_0",
        "content": json.dumps({
            "snapshot_id": "00000000-0000-0000-0000-000000000001",
            "kind": "daily_bars", "as_of": "2026-10-02T06:00:00+00:00",
            "mode": "forward", "row_count": 10,
        }),
    })
    body2 = {"model": model, "messages": messages, "tools": [TOOL_DEF], "max_tokens": 512}
    conn2, resp2 = open_stream(body2, key)
    try:
        if resp2.status != 200:
            print(f"roundtrip: FAIL status={resp2.status} body={resp2.read()[:300]!r}")
            return 1
        content2, _, chunks2, finish2 = drain_stream(resp2)
    finally:
        conn2.close()
    ok = "00000000-0000-0000-0000-000000000001" in content2
    print(f"roundtrip: chunks={chunks2} finish={finish2} answer={content2[:200]!r}")
    print(f"toolcall: {'PASS' if ok else 'OBSERVED(no id echo)'}")
    return 0


def cmd_cancel():
    key = env("GW_KEY")
    model = env("GW_MODEL", "glm-5.3")
    marker = f"cancel-test-{datetime.now(timezone.utc).isoformat()}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": (
            f"[{marker}] Write a very long, detailed story about a lighthouse "
            "keeper. At least 2000 words."
        )}],
        "max_tokens": 2048,
    }
    conn, resp = open_stream(body, key)
    if resp.status != 200:
        print(f"cancel: FAIL could not open stream status={resp.status}")
        conn.close()
        return 1
    received = {"chunks": 0, "chars": 0}
    t0 = time.monotonic()
    try:
        for payload in iter_sse(resp):
            received["chunks"] += 1
            for choice in payload.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    received["chars"] += len(delta["content"])
            if received["chunks"] >= 8:
                break
    except Exception as exc:
        print(f"cancel: stream read error before abort: {exc}")
    finally:
        conn.close()  # the client abort
    print(f"cancel: marker={marker}")
    print(f"  aborted after {time.monotonic() - t0:.1f}s chunks={received['chunks']} "
          f"chars={received['chars']}")
    print("cancel: CLIENT_ABORTED (correlate: gateway logs + spend rows near the "
          "marker timestamp; a spend row with partial completion tokens = "
          "upstream terminated; full-generation tokens = it kept generating)")
    return 0


def _one_request(key: str, body: dict):
    conn, resp = open_stream(body, key, timeout=90)
    try:
        if resp.status != 200:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, str(raw)[:200]
        _, _, chunks, finish = drain_stream(resp)
        return 200, f"ok chunks={chunks} finish={finish}"
    finally:
        conn.close()


def cmd_concurrency():
    limits_key = env("GW_LIMITS_KEY")
    model = env("GW_MODEL", "glm-5.3")
    if not limits_key:
        print("concurrency: FAIL GW_LIMITS_KEY not set")
        return 1
    long_body = {
        "model": model,
        "messages": [{"role": "user", "content": "Write a 1500-word essay on tides."}],
        "max_tokens": 2048,
    }
    small_body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say OK."}],
        "max_tokens": 8,
    }
    results = {}

    def run_long():
        results["long"] = _one_request(limits_key, long_body)

    t = threading.Thread(target=run_long)
    t.start()
    time.sleep(3.0)  # let the long one be solidly in flight
    t0 = time.monotonic()
    results["overlap"] = _one_request(limits_key, small_body)
    results["overlap_latency"] = round(time.monotonic() - t0, 1)
    t.join()
    print(f"concurrency(max_parallel_requests=1): long={results['long']}")
    print(f"  overlapping={results['overlap']} (after {results['overlap_latency']}s)")
    status = results["overlap"][0]
    if status == 429:
        print("concurrency: PASS (second request rejected while first in flight)")
        return 0
    print(f"concurrency: OBSERVED overlapping status={status} (limit did not fire)")
    return 1


def cmd_rate():
    limits_key = env("GW_LIMITS_KEY")
    model = env("GW_MODEL", "glm-5.3")
    if not limits_key:
        print("rate: FAIL GW_LIMITS_KEY not set")
        return 1
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say OK."}],
        "max_tokens": 8,
    }
    statuses = []
    for i in range(3):
        status, detail = _one_request(limits_key, body)
        statuses.append(status)
        print(f"rate(rpm_limit=2): request {i + 1} -> {status} {detail if status != 200 else ''}")
        time.sleep(1.0)
    if statuses[:2] == [200, 200] and statuses[2] == 429:
        print("rate: PASS (third request rejected in the same minute window)")
        return 0
    print(f"rate: OBSERVED statuses={statuses}")
    return 1


def cmd_tpm():
    limits_key = env("GW_LIMITS_KEY")
    model = env("GW_MODEL", "glm-5.3")
    if not limits_key:
        print("tpm: FAIL GW_LIMITS_KEY not set")
        return 1
    # ~300 short lines ≈ 4-5k prompt tokens: request 1 must fit under the
    # key's tpm_limit (projected tokens are checked PRE-admission — observed
    # 2026-10-02: a request whose projected usage exceeds the remaining TPM
    # is rejected before the upstream call), request 2 must exceed what is
    # left. Set the key's tpm_limit to 6000 for this shape.
    filler = "\n".join(f"context line {i}: the lighthouse log records a steady wind." for i in range(300))
    body = {
        "model": model,
        "messages": [{"role": "user", "content": f"Context:\n{filler}\n\nReply with the single word OK."}],
        "max_tokens": 8,
    }
    s1, d1 = _one_request(limits_key, body)
    print(f"tpm: request 1 (large prompt, prior spend 0) -> {s1}")
    time.sleep(2.0)
    s2, d2 = _one_request(limits_key, body)
    print(f"tpm: request 2 (same prompt, prior spend > limit) -> {s2} {d2 if s2 != 200 else ''}")
    if s1 == 200 and s2 == 429:
        print("tpm: PASS (second request rejected on token-rate)")
        return 0
    print(f"tpm: OBSERVED {s1} then {s2}")
    return 1


def cmd_spend():
    master = env("MASTER_KEY")
    if not master:
        print("spend: FAIL MASTER_KEY not set")
        return 1
    conn = connect()
    try:
        conn.request("GET", "/spend/logs?num=30", headers={"Authorization": f"Bearer {master}"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        status = resp.status
    finally:
        conn.close()
    if status != 200:
        print(f"spend: FAIL status={status} body={raw[:300]}")
        return 1
    data = json.loads(raw)
    if isinstance(data, dict):
        rows = data.get("data", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    print(f"spend: {len(rows)} recent rows")
    for r in rows:
        print(
            f"  start={r.get('startTime')} end={r.get('endTime')} "
            f"model={r.get('model')} key={str(r.get('api_key'))[:12]} "
            f"prompt={r.get('prompt_tokens')} completion={r.get('completion_tokens')} "
            f"dur={r.get('request_duration_ms')}ms cache_hit={r.get('cache_hit')} "
            f"status={r.get('status') if r.get('status') is not None else '-'}"
        )
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "models":
        return cmd_models()
    if cmd == "toolcall":
        return cmd_toolcall(no_stream="--no-stream" in sys.argv)
    if cmd == "cancel":
        return cmd_cancel()
    if cmd == "concurrency":
        return cmd_concurrency()
    if cmd == "rate":
        return cmd_rate()
    if cmd == "tpm":
        return cmd_tpm()
    if cmd == "spend":
        return cmd_spend()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
