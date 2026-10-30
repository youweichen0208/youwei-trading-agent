"""Exercise real fixed-Gateway cross-Profile auth and Run ownership without model credentials."""

import argparse
import json
import os
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def probe(phase):
    protocol.require(os.environ.get("YTA_HERMES_PROFILE_PROBE") == "1", "explicit_probe")
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    protocol.require(active == {"lo"}, "network_none")
    protocol.RECEIPT = Path("/opt/data/profile-isolation.json")
    routes = {"default": "", "alpha-orchestrator": "/p/alpha-orchestrator"}
    tokens = {
        "default": os.environ["API_SERVER_KEY"],
        "alpha-orchestrator": os.environ["YTA_PROFILE_PROBE_ALPHA_TOKEN"],
    }
    protocol.require(len(set(tokens.values())) == 2, "distinct_credentials")
    if phase == "submit":
        receipt = {
            "key": "yta-profiles-" + secrets.token_hex(16),
            "body": {"input": "Isolated authorization probe with no model credentials."},
            "runs": {},
        }
        protocol.save_receipt(receipt, initial=True)
    else:
        receipt = json.loads(protocol.RECEIPT.read_text())
    rejected = 0
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        for name, prefix in routes.items():
            own = {"Authorization": "Bearer " + tokens[name], "Idempotency-Key": receipt["key"]}
            other = next(token for profile, token in tokens.items() if profile != name)
            for token in ("", other):
                status, _, _ = protocol.request(
                    client,
                    "GET",
                    prefix + "/v1/models",
                    headers={"Authorization": "Bearer " + token if token else ""},
                )
                protocol.require(status == 401, "foreign_credential_rejected")
                rejected += 1
            status, _, _ = protocol.request(client, "GET", prefix + "/v1/models", headers=own)
            protocol.require(status == 200, "own_credential_accepted")
            status, raw, _ = protocol.request(
                client, "POST", prefix + "/v1/runs", body=receipt["body"], headers=own
            )
            protocol.require(status == 202, "admission")
            admitted = json.loads(raw)
            run_id = admitted["run_id"]
            if name in receipt["runs"]:
                protocol.require(
                    run_id == receipt["runs"][name] and admitted.get("replayed") is True,
                    "persisted_identity",
                )
            receipt["runs"][name] = run_id
            protocol.save_receipt(receipt)
            deadline = time.monotonic() + 25
            while True:
                status, raw, _ = protocol.request(
                    client, "GET", prefix + f"/v1/runs/{run_id}", headers=own
                )
                protocol.require(status == 200, "owned_status")
                outcome = json.loads(raw)
                if outcome.get("status") in ("failed", "completed", "cancelled", "unknown"):
                    break
                protocol.require(time.monotonic() < deadline, "terminal_deadline")
                time.sleep(0.2)
            protocol.require(outcome["status"] == "failed", "credential_free_failure")
        protocol.require(len(set(receipt["runs"].values())) == 2, "profile_scoped_idempotency")
        for name, prefix in routes.items():
            own = {"Authorization": "Bearer " + tokens[name], "Idempotency-Key": receipt["key"]}
            foreign_run = next(
                run_id for profile, run_id in receipt["runs"].items() if profile != name
            )
            for method, suffix in (
                ("GET", ""),
                ("GET", "/events"),
                ("POST", "/stop"),
                ("POST", "/resolve-unknown"),
            ):
                status, _, _ = protocol.request(
                    client,
                    method,
                    prefix + f"/v1/runs/{foreign_run}" + suffix,
                    body={} if method == "POST" else None,
                    headers=own,
                )
                protocol.require(status == 404, "foreign_run_hidden")
                rejected += 1
            status, raw, _ = protocol.request(
                client, "POST", prefix + "/v1/runs", body=receipt["body"], headers=own
            )
            replay = json.loads(raw)
            protocol.require(
                status == 202
                and replay["run_id"] == receipt["runs"][name]
                and replay.get("replayed") is True,
                "own_replay",
            )
            status, raw, _ = protocol.request(
                client,
                "POST",
                prefix + "/v1/runs",
                body={"input": "Different known test input"},
                headers=own,
            )
            protocol.require(
                status == 409 and json.loads(raw)["error"]["code"] == "idempotency_key_conflict",
                "own_conflict",
            )
        status, _, _ = protocol.request(
            client,
            "GET",
            "/p/not-configured/v1/models",
            headers={"Authorization": "Bearer " + tokens["default"]},
        )
        protocol.require(status == 404, "unknown_profile_rejected")
        cases = [
            ("/v1/models", tokens["default"], 200),
            ("/p/alpha-orchestrator/v1/models", tokens["alpha-orchestrator"], 200),
            ("/v1/models", tokens["alpha-orchestrator"], 401),
            ("/p/alpha-orchestrator/v1/models", tokens["default"], 401),
            ("/p/not-configured/v1/models", tokens["default"], 404),
            (
                "/p/alpha-orchestrator/v1/runs/" + receipt["runs"]["default"],
                tokens["alpha-orchestrator"],
                404,
            ),
        ]

        def check_concurrent(index):
            path, token, expected = cases[index % len(cases)]
            actual, _, _ = protocol.request(
                client, "GET", path, headers={"Authorization": "Bearer " + token}
            )
            protocol.require(actual == expected, "concurrent_profile_scope")
            return actual

        with ThreadPoolExecutor(max_workers=6) as pool:
            concurrent = list(pool.map(check_concurrent, range(36)))
    return {
        "phase": phase,
        "multiplex_profiles": 2,
        "auth_and_cross_profile_rejections": rejected,
        "same_key_isolated": True,
        "own_replay_and_conflict": True,
        "unknown_profile_rejected": True,
        "concurrent_requests_checked": len(concurrent),
        "concurrent_status_counts": {
            str(status): concurrent.count(status) for status in (200, 401, 404)
        },
        "external_model_requests": 0,
        "network": "none",
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
        raise SystemExit(json.dumps({"error": "PROFILE_PROBE_FAILED", "stage": stage})) from None
