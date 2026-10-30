"""Observe a lost real-model owner; acknowledge uncertainty without replaying its work."""

import argparse
import json
import os
import secrets
from pathlib import Path

import httpx
import probe_hermes_failure as protocol
from probe_hermes_cancel import wait_for_output


def read_status(client, run_id):
    status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
    protocol.require(status == 200, "read_status")
    return json.loads(raw)


def probe(phase):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_probe")
    protocol.RECEIPT = Path("/opt/data/pi-crash-run.json")
    if phase == "start":
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        receipt = {
            "key": "yta-pi-crash-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": (
                    "This is a streaming crash-recovery test. Do not call any tools. "
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
        if phase != "start":
            protocol.require(
                run_id == receipt["run_id"] and admitted.get("replayed") is True, "same_run"
            )
        receipt["run_id"] = run_id
        protocol.save_receipt(receipt)
        if phase == "start":
            try:
                receipt["first_delta_seq"] = wait_for_output(client, run_id)
                outcome = read_status(client, run_id)
                protocol.require(outcome["status"] == "running", "running_before_crash")
                receipt["resolution"] = {
                    key: outcome[key] for key in ("admission_id", "execution_generation")
                }
                protocol.save_receipt(receipt)
            except Exception:
                protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
                raise
            return {
                "ready_for_isolated_crash": True,
                "status": "running",
                "real_model_output_observed": True,
            }
        outcome = read_status(client, run_id)
        protocol.require(outcome["status"] == "unknown", "unknown_after_crash")
        protocol.require(
            all(outcome[key] == value for key, value in receipt["resolution"].items()),
            "same_execution_generation",
        )
        status, _, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        protocol.require(status == 404, "old_stream_unavailable")
        status, raw, _ = protocol.request(client, "POST", f"/v1/runs/{run_id}/stop")
        protocol.require(
            status == 409 and json.loads(raw)["error"]["code"] == "unknown_execution",
            "unknown_cannot_stop",
        )
        if phase == "observe":
            return {
                "status": "unknown",
                "same_key_replay": True,
                "generation_preserved": True,
                "stop_rejected": True,
                "sse_status": 404,
            }
        endpoint = f"/v1/runs/{run_id}/resolve-unknown"
        wrong = {
            **receipt["resolution"],
            "execution_generation": receipt["resolution"]["execution_generation"] + 1,
        }
        status, raw, _ = protocol.request(client, "POST", endpoint, body=wrong)
        protocol.require(
            status == 409 and json.loads(raw)["error"]["code"] == "stale_generation",
            "stale_generation_rejected",
        )
        protocol.require(
            read_status(client, run_id)["status"] == "unknown", "bad_resolution_no_effect"
        )
        for _ in range(2):
            status, _, _ = protocol.request(client, "POST", endpoint, body=receipt["resolution"])
            protocol.require(status == 200, "exact_resolution_idempotent")
        outcome = read_status(client, run_id)
        protocol.require(outcome["status"] == "cancelled", "resolution_interrupts_not_requeues")
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
        replay = json.loads(raw)
        protocol.require(
            status == 202 and replay.get("replayed") is True and replay.get("run_id") == run_id,
            "resolved_same_key_replay",
        )
        return {
            "real_provider": True,
            "observed_status_after_crash": "unknown",
            "resolved_status": outcome["status"],
            "stale_generation_rejected": True,
            "exact_resolution_idempotent": True,
            "same_key_replay": True,
            "old_stream_status": 404,
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["start", "observe", "resolve"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(json.dumps({"error": "CRASH_PROBE_FAILED", "stage": stage})) from None
