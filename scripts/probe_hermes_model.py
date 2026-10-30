"""Exercise a real locked Hermes Run with an isolated synthetic model and REPLAY tools."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol

TOOLS = ["mcp__yta_finance__market_snapshot", "mcp__yta_finance__indicators"]


def validate_events(events):
    completed = [event for event in events if event.get("event") == "tool.completed"]
    started = [event for event in events if event.get("event") == "tool.started"]
    protocol.require([event.get("tool") for event in completed] == TOOLS, "two_completed_tools")
    protocol.require([event.get("tool") for event in started] == TOOLS, "two_started_tools")
    protocol.require(all(event.get("error") is False for event in completed), "tool_success")
    ids = [event.get("tool_call_id") for event in started]
    protocol.require(
        all(isinstance(value, str) and value for value in ids)
        and len(set(ids)) == 2
        and ids == [event.get("tool_call_id") for event in completed],
        "tool_call_correlation",
    )
    protocol.require(events[-1].get("event") == "run.completed", "completed_sse")
    seq = [event["seq"] for event in events]
    protocol.require(seq == sorted(set(seq)), "sequences")
    return seq


def probe(phase, case="initial"):
    protocol.require(os.environ.get("YTA_MODEL_FIXTURE_MODE") == "REPLAY", "fixture_mode")
    protocol.require(
        os.environ.get("OPENAI_BASE_URL") == "http://model-fixture:8300/v1", "fixture_endpoint"
    )
    protocol.RECEIPT = Path(
        {
            "initial": "/opt/data/yta-model-probe.json",
            "discovery": "/opt/data/yta-model-discovery-probe.json",
            "direct": "/opt/data/yta-model-direct-probe.json",
        }[case]
    )
    protocol.LIMIT = 2 * 1024 * 1024
    if phase == "submit":
        receipt = {
            "key": "yta-model-fixture-" + secrets.token_hex(16),
            "body": {
                "input": "Call market_snapshot and indicators for this synthetic REPLAY probe."
            },
        }
        protocol.save_receipt(receipt, initial=True)
    else:
        receipt = json.loads(protocol.RECEIPT.read_text())
    with httpx.Client(
        timeout=10,
        trust_env=False,
        follow_redirects=False,
        headers={
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "Idempotency-Key": receipt["key"],
        },
    ) as client:
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
        protocol.require(status == 202, "admission")
        admitted = json.loads(raw)
        run_id = admitted["run_id"]
        if receipt.get("run_id"):
            protocol.require(receipt["run_id"] == run_id, "same_run")
            protocol.require(admitted.get("replayed") is True, "replayed_run")
        receipt["run_id"] = run_id
        protocol.save_receipt(receipt)
        deadline = time.monotonic() + 45
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in ("completed", "failed", "cancelled", "execution_unknown"):
                break
            protocol.require(time.monotonic() < deadline, "terminal_deadline")
            time.sleep(0.2)
        protocol.require(outcome["status"] == "completed", "successful_terminal")
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        protocol.require(status == 200, "sse_read")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        seq = validate_events(events)
        status, raw, _ = protocol.request(
            client, "GET", f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(seq[-1])}
        )
        protocol.require(status == 200 and b"data: " not in raw, "cursor_resume")
        response = client.get(
            "http://model-fixture:8300/stats",
            headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
        )
        stats = response.json()
        protocol.require(stats.get("tool_calls") == TOOLS, "fixture_tool_sequence")
        protocol.require(set(stats.get("models", [])) == {"yta-protocol-fixture"}, "fixture_model")
        return {
            "synthetic_model": True,
            "data_mode": "REPLAY",
            "terminal_status": outcome["status"],
            "tool_calls": TOOLS,
            "event_types": [event["event"] for event in events],
            "cursor_resume": True,
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify"])
    parser.add_argument("--case", choices=["initial", "discovery", "direct"], default="direct")
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase, args.case)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(json.dumps({"error": "MODEL_PROBE_FAILED", "stage": stage})) from None
