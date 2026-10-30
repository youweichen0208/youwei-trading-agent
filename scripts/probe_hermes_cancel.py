"""Cancel a real pi-model Run after observing output; preserve its idempotent identity."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def validate_cancel_events(events):
    kinds = [event.get("event") for event in events]
    protocol.require("message.delta" in kinds, "model_output_observed")
    protocol.require(kinds[-1:] == ["run.cancelled"], "cancelled_sse")
    protocol.require(
        not {"run.completed", "run.failed", "tool.started"}.intersection(kinds),
        "unexpected_execution",
    )
    seq = [event["seq"] for event in events]
    protocol.require(seq == sorted(set(seq)), "event_sequence")
    return seq


def wait_for_output(client, run_id):
    deadline = time.monotonic() + 45
    size = 0
    with client.stream(
        "GET", f"http://127.0.0.1:8642/v1/runs/{run_id}/events", timeout=45
    ) as response:
        protocol.require(response.status_code == 200, "live_sse")
        for line in response.iter_lines():
            size += len(line)
            protocol.require(size <= 65536 and time.monotonic() < deadline, "live_sse_bound")
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("event") == "message.delta" and event.get("delta"):
                return event["seq"]
            protocol.require(not event.get("event", "").startswith("run."), "terminal_before_stop")
    raise protocol.ProbeFailure("no_model_output")


def probe(phase):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_probe")
    protocol.RECEIPT = Path("/opt/data/pi-cancel-run.json")
    protocol.LIMIT = 2 * 1024 * 1024
    if phase == "submit":
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        receipt = {
            "key": "yta-pi-cancel-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": (
                    "This is a streaming cancellation test. Do not call any tools. "
                    "Write 200 numbered paragraphs explaining arithmetic to beginners. "
                    "Each paragraph should have 40 words. Begin immediately with paragraph 1."
                ),
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
                run_id == receipt["run_id"] and admitted.get("replayed") is True, "same_run"
            )
        receipt["run_id"] = run_id
        protocol.save_receipt(receipt)
        if phase == "submit":
            try:
                receipt["first_delta_seq"] = wait_for_output(client, run_id)
                protocol.save_receipt(receipt)
                status, _, _ = protocol.request(
                    client,
                    "POST",
                    f"/v1/runs/{run_id}/stop",
                    headers={"Authorization": "Bearer invalid"},
                )
                protocol.require(status == 401, "stop_authentication")
            except Exception:
                protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
                raise
        protocol.require("first_delta_seq" in receipt, "persisted_output_observation")
        status, raw, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
        protocol.require(status == 200, "stop_accepted")
        deadline = time.monotonic() + 45
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in ("cancelled", "completed", "failed", "execution_unknown"):
                break
            protocol.require(time.monotonic() < deadline, "cancel_deadline")
            time.sleep(0.2)
        protocol.require(outcome["status"] == "cancelled", "cancelled_terminal")
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        if phase == "restart":
            protocol.require(status == 404, "restart_stream_not_retained")
            event_types = []
        else:
            protocol.require(status == 200, "sse_read")
            events = [
                json.loads(line[6:])
                for line in raw.decode().splitlines()
                if line.startswith("data: ")
            ]
            seq = validate_cancel_events(events)
            status, raw, _ = protocol.request(
                client, "GET", f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(seq[-1])}
            )
            protocol.require(status == 200 and b"data: " not in raw, "cursor_resume")
            event_types = sorted({event["event"] for event in events})
        status, raw, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
        protocol.require(
            status == 200 and json.loads(raw).get("status") == "cancelled", "repeated_stop"
        )
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
        replay = json.loads(raw)
        protocol.require(
            status == 202 and replay.get("run_id") == run_id and replay.get("replayed") is True,
            "same_key_replay",
        )
        return {
            "phase": phase,
            "real_provider": True,
            "model_output_before_stop": True,
            "status": outcome["status"],
            "event_types": event_types,
            "same_key_replay": True,
            "repeat_stop_idempotent": True,
            "stream_retained": phase != "restart",
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify", "restart"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(json.dumps({"error": "CANCEL_PROBE_FAILED", "stage": stage})) from None
