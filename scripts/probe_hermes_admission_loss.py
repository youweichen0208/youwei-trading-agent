"""Drop a real Hermes admission response, then reconcile concurrent exact-key retries."""

import argparse
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def client_for(receipt):
    return httpx.Client(
        timeout=10,
        trust_env=False,
        follow_redirects=False,
        headers={
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "Idempotency-Key": receipt["key"],
            "X-Hermes-Session-Key": receipt["session_key"],
        },
    )


def lose_response(receipt):
    admitted = threading.Event()
    release = threading.Event()
    errors = []
    timing = {}

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                protocol.require(self.path == "/v1/runs", "proxy_path")
                size = int(self.headers.get("Content-Length", "0"))
                protocol.require(0 < size <= 4096, "proxy_request_size")
                body = json.loads(self.rfile.read(size))
                protocol.require(body == receipt["body"], "proxy_request_identity")
                with client_for(receipt) as client:
                    status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=body)
                protocol.require(status == 202, "initial_admission")
                response = json.loads(raw)
                timing["admitted"] = time.monotonic()
                protocol.require(response.get("replayed") is not True, "initial_not_replayed")
                receipt["admitted_run_id"] = response["run_id"]
                receipt["upstream_accepted"] = True
                protocol.save_receipt(receipt)
                admitted.set()
                # Send no response bytes. The actual client must time out while
                # Hermes continues the already-admitted real-model execution.
                release.wait(5)
                self.close_connection = True
            except Exception:
                errors.append("admission_proxy_failed")
                admitted.set()
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                client.post(f"http://127.0.0.1:{server.server_port}/v1/runs", json=receipt["body"])
        except httpx.ReadTimeout:
            timing["timeout"] = time.monotonic()
            timed_out = True
        else:
            timed_out = False
        protocol.require(admitted.wait(15), "admission_observation_deadline")
        protocol.require(not errors and receipt.get("upstream_accepted"), "upstream_acceptance")
        protocol.require(timed_out, "real_client_read_timeout")
        protocol.require(timing["admitted"] < timing["timeout"], "admitted_before_timeout")
        receipt["client_read_timeout"] = True
        protocol.save_receipt(receipt)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def probe(phase):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_pi_probe")
    protocol.RECEIPT = Path("/opt/data/pi-admission-loss-probe.json")
    if phase == "submit":
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        receipt = {
            "key": "yta-admission-loss-" + secrets.token_hex(16),
            "session_key": "yta-loss-session-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": "Protocol test. Reply exactly ACK. Do not use any tools.",
            },
        }
        protocol.save_receipt(receipt, initial=True)
        lose_response(receipt)
    else:
        receipt = json.loads(protocol.RECEIPT.read_text())
    protocol.require(receipt.get("client_read_timeout") is True, "lost_response_evidence")

    def replay(_):
        with client_for(receipt) as client:
            status, raw, headers = protocol.request(
                client, "POST", "/v1/runs", body=receipt["body"]
            )
        result = json.loads(raw)
        protocol.require(status == 202 and result.get("replayed") is True, "exact_key_replayed")
        protocol.require(
            headers.get("x-hermes-session-key") == receipt["session_key"], "session_identity"
        )
        return result["run_id"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(replay, range(4)))
    protocol.require(set(ids) == {receipt["admitted_run_id"]}, "single_admitted_run")
    receipt["concurrent_replays_verified"] = True
    protocol.save_receipt(receipt)
    run_id = receipt["admitted_run_id"]
    with client_for(receipt) as client:
        deadline = time.monotonic() + 150
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in (
                "completed",
                "failed",
                "cancelled",
                "unknown",
                "execution_unknown",
            ):
                break
            protocol.require(time.monotonic() < deadline, "observation_deadline")
            time.sleep(0.5)
        protocol.require(outcome.get("status") == "completed", "successful_terminal")
        protocol.require(outcome.get("output", "").strip() == "ACK", "real_model_answer")
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}/events")
        protocol.require(status == 200, "events")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        protocol.require(
            sum(e.get("event") == "run.completed" for e in events) == 1, "one_terminal"
        )
        protocol.require(not any(e.get("event") == "tool.started" for e in events), "no_tools")
        changed = {**receipt["body"], "input": "Different content must be rejected."}
        status, _, _ = protocol.request(client, "POST", "/v1/runs", body=changed)
        protocol.require(status == 409, "same_key_conflict")
    receipt["validated"] = True
    receipt["usage"] = outcome.get("usage")
    protocol.save_receipt(receipt)
    return {
        "real_provider": True,
        "requested_model": receipt["body"]["model"],
        "client_read_timeout": True,
        "upstream_admission_before_response_loss": True,
        "concurrent_exact_key_replays": len(ids),
        "same_run_identity": True,
        "same_key_changed_payload_status": 409,
        "status": outcome["status"],
        "completed_events": 1,
        "tool_calls": 0,
        "usage": outcome.get("usage"),
        "qualification_status": "UNVERIFIED",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "private_probe_failure"
        print(json.dumps({"error_code": "ADMISSION_LOSS_PROBE_FAILED", "stage": stage}))
        raise SystemExit(1) from None
