"""Locked Gateway mixed allowed/forbidden batch, synthetic model and REPLAY market."""

import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol

TOOLS = ["mcp__yta_finance__market_snapshot", "mcp__yta_finance__indicators"]
MARKER = "/tmp/yta-mixed-tool-effect"
FORBIDDEN = {
    "terminal": {"command": "touch " + MARKER},
    "read_file": {"path": "/opt/data/profiles/alpha-orchestrator/config.yaml"},
    "write_file": {"path": MARKER, "content": "unexpected execution"},
    "delegate_task": {"task": "Write a file at " + MARKER},
}
SUCCESS = "MIXED_BATCH_REJECTED_AND_FINANCE_COMPLETED"


def answer(body):
    advertised = {t["function"]["name"] for t in body.get("tools", [])}
    if advertised != set(TOOLS):
        raise ValueError("Unexpected catalog")
    results = [m for m in body["messages"] if m.get("role") == "tool"]
    if not results:
        calls = {TOOLS[0]: {}, **FORBIDDEN, TOOLS[1]: {}}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_mixed_" + str(i),
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(calls.items())
            ],
        }, "tool_calls"
    if len(results) != 6:
        raise ValueError("Incomplete batch")
    by_id = {m["tool_call_id"]: m["content"] for m in results}
    for i, name in enumerate(FORBIDDEN, start=1):
        expected = f"Tool '{name}' does not exist. Available tools: " + ", ".join(sorted(TOOLS))
        if by_id["call_mixed_" + str(i)].strip() != expected:
            raise ValueError("Forbidden call was not explicitly rejected")
    for i, name in zip((0, 5), TOOLS, strict=True):
        content = by_id["call_mixed_" + str(i)]
        if not content.startswith(f'<untrusted_tool_result source="{name}">'):
            raise ValueError("Missing untrusted result wrapper")
        if "<persisted-output>" not in content or "Preview (first 1500 chars):" not in content:
            raise ValueError("Missing archived finance result")
    return {"role": "assistant", "content": SUCCESS}, "stop"


def probe():
    protocol.RECEIPT = Path("/opt/data/yta-mixed-tool-probe-v2.json")
    protocol.LIMIT = 2 * 1024 * 1024
    fresh = not protocol.RECEIPT.exists()
    if fresh:
        protocol.save_receipt({"key": secrets.token_hex(16)}, initial=True)
    receipt = json.loads(protocol.RECEIPT.read_text())
    with httpx.Client(
        timeout=10,
        trust_env=False,
        headers={
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "Idempotency-Key": receipt["key"],
        },
    ) as client:
        if "run_id" not in receipt:
            protocol.require(fresh, "unknown_admission_no_resubmit")
            status, raw, _ = protocol.request(
                client,
                "POST",
                "/v1/runs",
                body={"input": "Exercise the synthetic mixed tool batch using REPLAY data."},
            )
            protocol.require(status == 202, "admission")
            receipt["run_id"] = json.loads(raw)["run_id"]
            protocol.save_receipt(receipt)
        path = "/v1/runs/" + receipt["run_id"]
        deadline = time.monotonic() + 45
        while True:
            status, raw, _ = protocol.request(client, "GET", path)
            protocol.require(status == 200, "status")
            outcome = json.loads(raw)
            if outcome["status"] in ("completed", "failed", "cancelled", "unknown"):
                break
            protocol.require(time.monotonic() < deadline, "observation_deadline")
            time.sleep(0.2)
        protocol.require(outcome["status"] == "completed", "completed")
        protocol.require(outcome["output"] == SUCCESS, "validated_model_results")
        status, raw, _ = protocol.request(client, "GET", path + "/events")
        protocol.require(status == 200, "events")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        for kind in ("tool.started", "tool.completed"):
            selected = [e for e in events if e.get("event") == kind]
            protocol.require(
                len(selected) == 2 and {e["tool"] for e in selected} == set(TOOLS),
                "only_allowed_dispatch",
            )
            if kind == "tool.completed":
                protocol.require(all(e["error"] is False for e in selected), "finance_success")
        protocol.require(not Path(MARKER).exists(), "no_file_side_effect")
        for i in (0, 5):
            archived = Path(
                f"/opt/data/profiles/alpha-orchestrator/cache/spillover/call_mixed_{i}.txt"
            )
            result = json.loads(archived.read_text())
            mode = result["snapshot"]["data_mode"] if i == 0 else result["data_mode"]
            protocol.require(mode == "REPLAY" and "error" not in result, "archived_finance_result")
        return {
            "run_id": receipt["run_id"],
            "terminal_status": outcome["status"],
            "allowed_tools_completed": TOOLS,
            "forbidden_tools_rejected": list(FORBIDDEN),
            "forbidden_tool_dispatch_events": 0,
            "filesystem_side_effect_absent": True,
            "synthetic_model": True,
            "data_mode": "REPLAY",
            "external_model_requests": 0,
            "qualification_status": "UNVERIFIED",
            "scope": "Single locked Gateway mixed batch; not OS sandbox or full release gate",
        }


if __name__ == "__main__":
    if os.environ.get("YTA_MIXED_TOOL_PROBE") != "1":
        raise SystemExit("Explicit isolated probe required")
    if sys.argv[1] == "model":
        import hermes_model_fixture as fixture

        fixture.answer = answer
        fixture.main()
    elif sys.argv[1] == "probe":
        print(json.dumps(probe()))
    else:
        raise SystemExit("Unknown phase")
