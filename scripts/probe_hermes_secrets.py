"""Positive real-model control versus an authenticated Profile without model credentials."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def probe(phase):
    protocol.require(os.environ.get("YTA_HERMES_SECRET_PROBE") == "1", "explicit_probe")
    protocol.RECEIPT = Path("/opt/data/model-secret-scope.json")
    if phase == "submit":
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        receipt = {
            "key": "yta-secrets-" + secrets.token_hex(16),
            "body": {
                "model": route["model"],
                "input": "Reply exactly YTA_SCOPE_OK. Do not use tools.",
            },
            "runs": {},
        }
        protocol.save_receipt(receipt, initial=True)
    else:
        receipt = json.loads(protocol.RECEIPT.read_text())
    results = {}
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        for name, prefix, token in (
            ("default", "", os.environ["API_SERVER_KEY"]),
            (
                "alpha-orchestrator",
                "/p/alpha-orchestrator",
                os.environ["YTA_PROFILE_PROBE_ALPHA_TOKEN"],
            ),
        ):
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": receipt["key"]}
            status, raw, _ = protocol.request(
                client, "POST", prefix + "/v1/runs", body=receipt["body"], headers=headers
            )
            protocol.require(status == 202, "profile_admission")
            admitted = json.loads(raw)
            run_id = admitted["run_id"]
            if name in receipt["runs"]:
                protocol.require(
                    receipt["runs"][name] == run_id and admitted.get("replayed") is True,
                    "same_identity",
                )
            receipt["runs"][name] = run_id
            protocol.save_receipt(receipt)
            deadline = time.monotonic() + 120
            while True:
                status, raw, _ = protocol.request(
                    client, "GET", prefix + f"/v1/runs/{run_id}", headers=headers
                )
                protocol.require(status == 200, "profile_status")
                outcome = json.loads(raw)
                if outcome.get("status") in ("completed", "failed", "cancelled", "unknown"):
                    break
                protocol.require(time.monotonic() < deadline, "observation_deadline")
                time.sleep(0.5)
            expected = "completed" if name == "default" else "failed"
            protocol.require(outcome["status"] == expected, "model_credential_boundary")
            if name == "default":
                protocol.require(
                    outcome.get("output", "").strip() == "YTA_SCOPE_OK", "real_model_control"
                )
            else:
                protocol.require(
                    "HTTP 401" in outcome.get("output", "")
                    and "YTA_SCOPE_OK" not in outcome.get("output", "")
                    and outcome.get("usage", {}).get("total_tokens") == 0,
                    "provider_authentication_rejected",
                )
            results[name] = {"status": outcome["status"], "usage": outcome.get("usage")}
        protocol.require(len(set(receipt["runs"].values())) == 2, "distinct_profile_runs")
    return {
        "phase": phase,
        "real_model_positive_control": True,
        "uncredentialed_profile_failed": True,
        "profiles": results,
        "qualification_status": "UNVERIFIED",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["submit", "verify"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(
            json.dumps({"error": "SECRET_SCOPE_PROBE_FAILED", "stage": stage})
        ) from None
