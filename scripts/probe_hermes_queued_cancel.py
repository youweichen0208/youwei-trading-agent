"""Cancel a queued turn behind a real pi-model stream in one declared session."""

import json
import os
import secrets
import time
from pathlib import Path

import probe_hermes_failure as protocol
from probe_hermes_admission_loss import client_for
from probe_hermes_cancel import wait_for_output


def admit(receipt, name):
    turn = receipt[name]
    with client_for({**turn, "session_key": receipt["session_key"]}) as client:
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=turn["body"])
    protocol.require(status == 202, name + "_admission")
    response = json.loads(raw)
    if turn.get("run_id"):
        protocol.require(turn["run_id"] == response["run_id"], "same_turn_identity")
    turn["run_id"] = response["run_id"]
    protocol.save_receipt(receipt)
    return turn["run_id"]


def status_of(client, run_id):
    status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
    protocol.require(status == 200, "status_read")
    return json.loads(raw)


def stop(client, run_id):
    status, _, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
    protocol.require(status == 200, "stop_accepted")
    deadline = time.monotonic() + 30
    while True:
        outcome = status_of(client, run_id)
        if outcome.get("status") in (
            "cancelled",
            "completed",
            "failed",
            "unknown",
            "execution_unknown",
        ):
            return outcome
        protocol.require(time.monotonic() < deadline, "stop_deadline")
        time.sleep(0.2)


def probe():
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_pi_probe")
    protocol.RECEIPT = Path("/opt/data/pi-queued-cancel-probe.json")
    route = json.loads(Path("/opt/data/pi-route.json").read_text())
    receipt = {
        "session_key": "yta-queued-session-" + secrets.token_hex(16),
        "first": {
            "key": "yta-queued-first-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": (
                    "Streaming test. Do not use tools. Write 200 numbered paragraphs about "
                    "arithmetic, 40 words each. Begin immediately."
                ),
            },
        },
        "queued": {
            "key": "yta-queued-second-" + secrets.token_hex(16),
            "body": {"model": route["model"], "input": "Reply exactly QUEUED. Do not use tools."},
        },
    }
    # Never invent replacement identities if this probe was interrupted.
    protocol.save_receipt(receipt, initial=True)
    with client_for({**receipt["first"], "session_key": receipt["session_key"]}) as client:
        try:
            first = admit(receipt, "first")
            receipt["first_delta_seq"] = wait_for_output(client, first)
            protocol.save_receipt(receipt)
            queued = admit(receipt, "queued")
            before = status_of(client, queued)
            receipt["queued_status_before_stop"] = before.get("status")
            protocol.save_receipt(receipt)
            protocol.require(before.get("status") == "queued", "queued_before_stop")
            cancelled = stop(client, queued)
            protocol.require(cancelled.get("status") == "cancelled", "queued_cancelled")
            protocol.require(stop(client, queued).get("status") == "cancelled", "repeat_stop")
            protocol.require(admit(receipt, "queued") == queued, "queued_exact_key_replay")
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{queued}/events")
            protocol.require(status == 200, "queued_events")
            events = [
                json.loads(line[6:])
                for line in raw.decode().splitlines()
                if line.startswith("data: ")
            ]
            kinds = [event.get("event") for event in events]
            protocol.require(kinds[-1:] == ["run.cancelled"], "queued_cancelled_event")
            protocol.require(
                not {"message.delta", "tool.started", "run.completed"}.intersection(kinds),
                "queued_did_not_execute",
            )
            first_outcome = stop(client, first)
            protocol.require(first_outcome.get("status") == "cancelled", "first_cancelled")
            # Releasing the first turn must not revive the cancelled queued turn.
            protocol.require(status_of(client, queued).get("status") == "cancelled", "no_revival")
            receipt["validated"] = True
            protocol.save_receipt(receipt)
            return {
                "real_provider": True,
                "requested_model": route["model"],
                "first_model_output_observed": True,
                "queued_status_before_stop": before["status"],
                "queued_final_status": cancelled["status"],
                "first_final_status": first_outcome["status"],
                "queued_event_types": sorted(set(kinds)),
                "repeat_stop_idempotent": True,
                "same_key_original_run": True,
                "qualification_status": "UNVERIFIED",
            }
        finally:
            for name in ("queued", "first"):
                if receipt[name].get("run_id"):
                    stop(client, receipt[name]["run_id"])


if __name__ == "__main__":
    try:
        print(json.dumps(probe()))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "private_probe_failure"
        print(json.dumps({"error_code": "QUEUED_CANCEL_PROBE_FAILED", "stage": stage}))
        raise SystemExit(1) from None
