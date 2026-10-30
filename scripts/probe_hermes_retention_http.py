"""HTTP expiry test on an isolated credential-free Gateway; age only its disposable fixture row."""

import json
import os
import socket
import sqlite3
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def main():
    protocol.require(os.environ.get("YTA_HERMES_RETENTION_HTTP_PROBE") == "1", "explicit_probe")
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    protocol.require(active == {"lo"}, "network_isolation")
    protocol.require(
        not any(
            os.environ.get(key)
            for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY")
        ),
        "no_model_credentials",
    )
    original = json.loads(protocol.RECEIPT.read_text())
    original_run = original["run_id"]
    # Exclusive evidence receipt prevents accidentally repeating the deliberate expiry experiment.
    protocol.RECEIPT = Path("/opt/data/yta-retention-http.json")
    receipt = {"original_run_id": original_run, "key": original["key"], "body": original["body"]}
    protocol.save_receipt(receipt, initial=True)
    with httpx.Client(
        timeout=5,
        trust_env=False,
        follow_redirects=False,
        headers={
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "Idempotency-Key": original["key"],
        },
    ) as client:
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{original_run}")
        protocol.require(
            status == 200 and json.loads(raw)["status"] == "failed", "known_terminal_only"
        )
        with sqlite3.connect("/opt/data/runs_idempotency.db") as conn:
            row = conn.execute(
                "SELECT status_json,retention_until,acknowledged_at "
                "FROM run_idempotency WHERE run_id=?",
                (original_run,),
            ).fetchone()
            protocol.require(
                row is not None
                and json.loads(row[0])["status"] == "failed"
                and row[1] == 0
                and row[2] is None,
                "default_terminal_fixture",
            )
            changed = conn.execute(
                "UPDATE run_idempotency SET updated_at=? WHERE run_id=?",
                (time.time() - 86401, original_run),
            ).rowcount
            protocol.require(changed == 1, "single_fixture_aged")
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=original["body"])
        protocol.require(status == 202, "expired_submission")
        admitted = json.loads(raw)
        new_run = admitted["run_id"]
        protocol.require(
            admitted.get("replayed") is False and new_run != original_run, "expired_key_new_run"
        )
        receipt["new_run_id"] = new_run
        protocol.save_receipt(receipt)
        deadline = time.monotonic() + 20
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{new_run}")
            protocol.require(status == 200, "new_run_status")
            outcome = json.loads(raw)
            if outcome.get("status") in ("failed", "completed", "cancelled", "unknown"):
                break
            protocol.require(time.monotonic() < deadline, "terminal_deadline")
            time.sleep(0.2)
        protocol.require(outcome["status"] == "failed", "credential_free_failure")
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=original["body"])
        repeated = json.loads(raw)
        protocol.require(
            status == 202 and repeated.get("replayed") is True and repeated["run_id"] == new_run,
            "new_record_replays",
        )
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{original_run}")
        protocol.require(
            status == 200 and json.loads(raw)["status"] == "failed", "old_run_still_queryable"
        )
    print(
        json.dumps(
            {
                "actual_http": True,
                "fixture_terminal_timestamp_aged": True,
                "default_retention_seconds": 86400,
                "same_key_after_expiry_created_new_run": True,
                "new_record_replayed": True,
                "old_run_status_still_queryable": True,
                "both_run_statuses": "failed",
                "external_model_requests": 0,
                "network": "none",
                "qualification_status": "UNVERIFIED",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(
            json.dumps({"error": "RETENTION_HTTP_PROBE_FAILED", "stage": stage})
        ) from None
