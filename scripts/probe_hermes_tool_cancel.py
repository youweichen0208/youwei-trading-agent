"""Observe cancellation during a real pi-model initiated, controlled MCP tool call."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def validate_tool_events(events):
    kinds = [event.get("event") for event in events]
    protocol.require(
        kinds == ["tool.started", "tool.completed", "run.cancelled"], "tool_cancel_events"
    )
    started, completed, _ = events
    protocol.require(
        started.get("tool") == completed.get("tool") == "mcp__yta_cancel_probe__delayed_diagnostic"
        and bool(started.get("tool_call_id"))
        and started["tool_call_id"] == completed.get("tool_call_id"),
        "correlated_tool",
    )
    protocol.require(completed.get("error") is True, "interrupted_tool_not_success")
    seq = [event["seq"] for event in events]
    protocol.require(seq == sorted(set(seq)), "ordered_events")
    return kinds


def probe(phase):
    protocol.require(os.environ.get("YTA_TOOL_CANCEL_PROBE") == "1", "explicit_probe")
    protocol.RECEIPT = Path("/opt/data/tool-cancel-receipt.json")
    if phase == "submit":
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        receipt = {
            "key": "yta-tool-cancel-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": (
                    "Call the delayed_diagnostic tool exactly once with no arguments. "
                    "This is a controlled cancellation test, not financial research. "
                    "Do not call any other tools. After it returns, say DIAGNOSTIC_COMPLETE."
                ),
            },
        }
        protocol.save_receipt(receipt, initial=True)
    else:
        receipt = json.loads(protocol.RECEIPT.read_text())
    with (
        httpx.Client(
            timeout=10,
            trust_env=False,
            headers={
                "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
                "Idempotency-Key": receipt["key"],
            },
        ) as client,
        httpx.Client(
            timeout=10,
            trust_env=False,
            base_url="http://tool-fixture:8200",
            headers={"Authorization": "Bearer " + os.environ["YTA_FINANCE_MCP_TOKEN"]},
        ) as fixture,
    ):
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
        protocol.require(status == 202, "admission")
        admitted = json.loads(raw)
        run_id = admitted["run_id"]
        if receipt.get("run_id"):
            protocol.require(
                run_id == receipt["run_id"] and admitted.get("replayed") is True, "original_run"
            )
        receipt["run_id"] = run_id
        protocol.save_receipt(receipt)

        def get_status():
            code, body, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(code == 200, "status")
            return json.loads(body)["status"]

        if phase == "submit":
            try:
                deadline = time.monotonic() + 60
                while True:
                    state = fixture.get("/stats").raise_for_status().json()
                    if state["started"]:
                        break
                    protocol.require(
                        get_status() not in ("completed", "failed", "cancelled"),
                        "terminal_before_tool",
                    )
                    protocol.require(time.monotonic() < deadline, "tool_not_started")
                    time.sleep(0.2)
                protocol.require(
                    state == {"started": 1, "finished": 0, "cancelled": 0, "timed_out": 0},
                    "inflight_tool",
                )
                receipt["before_stop"] = state
                receipt["status_before_stop"] = get_status()
                protocol.save_receipt(receipt)
                code, _, _ = protocol.request(
                    client,
                    "POST",
                    f"/v1/runs/{run_id}/stop",
                    headers={"Authorization": "Bearer invalid"},
                )
                protocol.require(code == 401, "stop_auth")
                code, _, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
                protocol.require(code == 200, "stop")
                time.sleep(2)
                receipt["after_stop_before_release"] = (
                    fixture.get("/stats").raise_for_status().json()
                )
                receipt["status_before_release"] = get_status()
                protocol.save_receipt(receipt)
            finally:
                fixture.post("/release").raise_for_status()
        deadline = time.monotonic() + 45
        while get_status() not in ("cancelled", "completed", "failed", "unknown"):
            protocol.require(time.monotonic() < deadline, "terminal_deadline")
            time.sleep(0.2)
        protocol.require(get_status() == "cancelled", "cancelled")
        code, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        protocol.require(code == 200, "events")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        kinds = validate_tool_events(events)
        code, _, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
        protocol.require(code == 200, "repeat_stop")
        code, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
        protocol.require(
            code == 202
            and json.loads(raw)["run_id"] == run_id
            and json.loads(raw).get("replayed") is True,
            "same_key",
        )
        after = fixture.get("/stats").raise_for_status().json()
        protocol.require(
            after["started"] == 1 and after["timed_out"] == 0, "single_tool_no_timeout"
        )
        receipt["after_release"] = after
        protocol.save_receipt(receipt)
        return {
            "real_pi_model": True,
            "diagnostic_tool_only": True,
            "status": "cancelled",
            "before_stop": receipt["before_stop"],
            "after_stop_before_release": receipt["after_stop_before_release"],
            "status_before_release": receipt["status_before_release"],
            "after_release": after,
            "event_types": kinds,
            "tool_completion_error": True,
            "tool_call_id_correlated": True,
            "same_key_replay": True,
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["submit", "verify"])
    try:
        print(json.dumps(probe(parser.parse_args().phase)))
    except Exception as error:
        raise SystemExit(
            json.dumps(
                {
                    "error": "TOOL_CANCEL_PROBE_FAILED",
                    "type": type(error).__name__,
                    "stage": str(error)
                    if isinstance(error, protocol.ProbeFailure)
                    else "private_diagnostic_required",
                }
            )
        ) from None
