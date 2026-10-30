"""Verify a real pi-selected model calling private REPLAY finance tools via Hermes."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol

TOOLS = {"mcp__yta_finance__market_snapshot", "mcp__yta_finance__indicators"}


def verify_events(events):
    starts = [event for event in events if event.get("event") == "tool.started"]
    ends = [event for event in events if event.get("event") == "tool.completed"]
    protocol.require(len(starts) == len(ends) == 2, "exactly_two_tools")
    protocol.require({event.get("tool") for event in ends} == TOOLS, "finance_tools")
    protocol.require(all(event.get("error") is False for event in ends), "successful_tools")
    start_ids = {event.get("tool_call_id"): event.get("tool") for event in starts}
    end_ids = {event.get("tool_call_id"): event.get("tool") for event in ends}
    protocol.require(len(start_ids) == 2 and all(start_ids) and start_ids == end_ids, "tool_ids")
    protocol.require(events[-1].get("event") == "run.completed", "completed_event")
    seq = [event["seq"] for event in events]
    protocol.require(seq == sorted(set(seq)), "sequence")
    return seq


def probe(phase, case="pi-settings"):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_probe")
    route = json.loads(Path("/opt/data/pi-route.json").read_text())
    protocol.RECEIPT = Path(
        "/opt/data/pi-real-model-run.json"
        if case == "initial"
        else "/opt/data/pi-settings-model-run.json"
    )
    protocol.LIMIT = 2 * 1024 * 1024
    if phase == "submit":
        receipt = {
            "key": "yta-pi-real-" + secrets.token_hex(16),
            "body": {
                "input": (
                    "This is a protocol integration test using synthetic REPLAY finance data. "
                    "Call mcp__yta_finance__market_snapshot exactly once with {}, then call "
                    "mcp__yta_finance__indicators exactly once with {}. "
                    "Do not call any other tool. "
                    "After both results, reply in at most 40 words: name the symbol, data mode and "
                    "whether the snapshot hashes match. "
                    "Explicitly say the data is synthetic REPLAY. "
                    "Do not forecast prices or claim live market data."
                ),
                "model": route["model"],
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
            protocol.require(
                receipt["run_id"] == run_id and admitted.get("replayed") is True, "same_run_replay"
            )
        receipt["run_id"] = run_id
        protocol.save_receipt(receipt)
        deadline = time.monotonic() + 150
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in ("completed", "failed", "cancelled", "execution_unknown"):
                break
            protocol.require(time.monotonic() < deadline, "observation_deadline")
            time.sleep(0.5)
        protocol.require(outcome["status"] == "completed", "successful_terminal")
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        protocol.require(status == 200, "sse")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        seq = verify_events(events)
        protocol.require("REPLAY" in json.dumps(outcome.get("output")), "replay_disclosure")
        status, raw, _ = protocol.request(
            client, "GET", f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(seq[-1])}
        )
        protocol.require(status == 200 and b"data: " not in raw, "cursor_resume")
        return {
            "real_provider": True,
            "source_provider": route["source_provider"],
            "requested_model": route["model"],
            "financial_data_mode": "REPLAY",
            "status": outcome["status"],
            "tool_names": sorted(TOOLS),
            "event_types": sorted({event["event"] for event in events}),
            "tool_calls_correlated": True,
            "cursor_resume": True,
            "replay_disclosed": True,
            "usage": outcome.get("usage"),
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify"])
    parser.add_argument("--case", choices=["initial", "pi-settings"], default="pi-settings")
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase, args.case)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(json.dumps({"error": "PI_MODEL_PROBE_FAILED", "stage": stage})) from None
