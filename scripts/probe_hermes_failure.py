"""Run only inside an isolated, network-none Hermes fixture with no model credentials."""

import argparse
import json
import os
import secrets
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

RECEIPT = Path("/opt/data/yta-failure-probe.json")
LIMIT = 65536


class ProbeFailure(RuntimeError):
    pass


def require(condition, stage):
    if not condition:
        raise ProbeFailure(stage)


def request(client, method, path, *, body=None, headers=None):
    with client.stream(
        method, "http://127.0.0.1:8642" + path, json=body, headers=headers
    ) as response:
        raw = bytearray()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            require(len(raw) <= LIMIT, "response_limit")
        return response.status_code, bytes(raw), dict(response.headers)


def save_receipt(receipt, *, initial=False):
    path = RECEIPT if initial else RECEIPT.with_suffix(".pending")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(receipt, file)
        file.flush()
        os.fsync(file.fileno())
    if not initial:
        os.replace(path, RECEIPT)
    directory = os.open(RECEIPT.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def probe(phase):
    require(os.environ.get("YTA_HERMES_FAILURE_PROBE") == "1", "explicit_probe_mode")
    active_interfaces = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    require(active_interfaces == {"lo"}, "network_not_isolated")
    require(
        not any(
            os.environ.get(key)
            for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY")
        ),
        "model_credential_present",
    )
    token = os.environ["API_SERVER_KEY"]
    if phase == "submit":
        receipt = {
            "key": "yta-failure-" + secrets.token_hex(16),
            "body": {
                "input": "YTA isolated protocol fixture. No model credentials are configured."
            },
        }
        save_receipt(receipt, initial=True)
    else:
        receipt = json.loads(RECEIPT.read_text())
    headers = {
        "Authorization": "Bearer " + token,
        "Accept-Encoding": "identity",
        "Idempotency-Key": receipt["key"],
    }
    with httpx.Client(
        timeout=5, trust_env=False, follow_redirects=False, headers=headers
    ) as client:
        if phase in ("submit", "verify", "replay"):
            for credential in ("", "Bearer invalid-fixture"):
                status, _, _ = request(
                    client,
                    "POST",
                    "/v1/runs",
                    body=receipt["body"],
                    headers={"Authorization": credential},
                )
                require(status == 401, "unauthorized_submission")
        status, raw, _ = request(client, "POST", "/v1/runs", body=receipt["body"])
        require(status == 202, "admission_status")
        admitted = json.loads(raw)
        run_id = admitted["run_id"]
        require(admitted["replayed"] is (phase != "submit"), "admission_replay_flag")
        if receipt.get("run_id"):
            require(run_id == receipt["run_id"], "restart_run_identity")
        receipt["run_id"] = run_id
        save_receipt(receipt)
        deadline = time.monotonic() + 25
        while True:
            status, raw, _ = request(client, "GET", f"/v1/runs/{run_id}")
            require(status == 200, "run_status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in ("failed", "completed", "cancelled", "execution_unknown"):
                break
            require(time.monotonic() < deadline, "terminal_deadline")
            time.sleep(0.2)
        require(outcome["status"] == "failed", "expected_credential_free_failure")
        status, raw, duplicate_headers = request(client, "POST", "/v1/runs", body=receipt["body"])
        duplicate = json.loads(raw)
        require(
            status == 202 and duplicate["run_id"] == run_id and duplicate["replayed"] is True,
            "same_key_replay",
        )
        require(duplicate_headers.get("idempotency-replayed") == "true", "replay_header")
        changed = {**receipt["body"], "input": "Different isolated fixture input"}
        status, raw, _ = request(client, "POST", "/v1/runs", body=changed)
        require(
            status == 409 and json.loads(raw)["error"]["code"] == "idempotency_key_conflict",
            "same_key_conflict",
        )
        sse_status, raw, _ = request(client, "GET", f"/v1/runs/{run_id}/events")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        if phase != "replay":
            require(
                sse_status == 200 and any(event.get("event") == "run.failed" for event in events),
                "failure_sse",
            )
            sequences = [event["seq"] for event in events]
            require(sequences == sorted(set(sequences)), "event_sequence")
            resumed_status, resumed_raw, _ = request(
                client,
                "GET",
                f"/v1/runs/{run_id}/events",
                headers={"Last-Event-ID": str(sequences[-1])},
            )
            require(
                resumed_status == 200 and b"data: " not in resumed_raw, "terminal_cursor_resume"
            )
        else:
            require(sse_status == 404, "restart_stream_not_retained")
        return {
            "phase": phase,
            "observed_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "terminal_status": outcome["status"],
            "same_key_replay": True,
            "unauthorized_submission_rejected": True,
            "terminal_cursor_resume": phase != "replay",
            "changed_body_conflict": True,
            "sse_status": sse_status,
            "event_types": [event.get("event") for event in events],
            "qualification_status": "UNVERIFIED",
            "external_network": "none",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify", "replay"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, ProbeFailure) else "probe_error"
        raise SystemExit(
            json.dumps({"error": "HERMES_FAILURE_PROBE_FAILED", "stage": stage})
        ) from None
