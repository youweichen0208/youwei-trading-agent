"""Verify real-model context continuity and independent session separation."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import probe_hermes_failure as protocol


def make_receipt(model):
    marker = "YTA-CONTEXT-" + secrets.token_hex(16)
    prompts = {
        "seed": (
            f"Remember this exact marker for later turns: {marker}. "
            "Reply exactly ACK. Do not use tools."
        ),
        "recall": (
            "Return the exact marker I asked you to remember earlier. No explanation or tools."
        ),
        "isolated": (
            "If an earlier user message in this conversation gave a YTA-CONTEXT- marker, "
            "reply with that marker. Otherwise reply exactly NONE. Do not use tools."
        ),
        "restart": (
            "Return only the exact marker remembered from my earlier message. Do not use tools."
        ),
    }
    return {
        "marker": marker,
        "session_key": "yta-context-" + secrets.token_hex(16),
        "isolated_key": "yta-isolated-" + secrets.token_hex(16),
        "turns": {
            phase: {
                "key": "yta-session-" + secrets.token_hex(16),
                "body": {"model": model, "input": prompt},
            }
            for phase, prompt in prompts.items()
        },
    }


def probe(phase):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_probe")
    protocol.RECEIPT = Path("/opt/data/pi-session-probe.json")
    if phase == "seed" and not protocol.RECEIPT.exists():
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        protocol.save_receipt(make_receipt(route["model"]), initial=True)
    receipt = json.loads(protocol.RECEIPT.read_text())
    turn = receipt["turns"][phase]
    if phase != "seed":
        protocol.require(receipt["turns"]["seed"].get("validated") is True, "seed_not_validated")
    session_key = receipt["isolated_key" if phase == "isolated" else "session_key"]
    with httpx.Client(
        timeout=10,
        trust_env=False,
        follow_redirects=False,
        headers={
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "Idempotency-Key": turn["key"],
            "X-Hermes-Session-Key": session_key,
        },
    ) as client:
        status, raw, headers = protocol.request(client, "POST", "/v1/runs", body=turn["body"])
        protocol.require(
            status == 202 and headers.get("x-hermes-session-key") == session_key, "admission"
        )
        admitted = json.loads(raw)
        run_id = admitted["run_id"]
        if turn.get("run_id"):
            protocol.require(
                turn["run_id"] == run_id and admitted.get("replayed") is True, "replay_identity"
            )
        protocol.require(
            all(
                other.get("run_id") != run_id
                for name, other in receipt["turns"].items()
                if name != phase
            ),
            "distinct_turn_ids",
        )
        turn["run_id"] = run_id
        protocol.save_receipt(receipt)
        deadline = time.monotonic() + 150
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{run_id}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in (
                "completed",
                "cancelled",
                "failed",
                "unknown",
                "execution_unknown",
            ):
                break
            protocol.require(time.monotonic() < deadline, "observation_deadline")
            time.sleep(0.5)
        protocol.require(outcome["status"] == "completed", "successful_terminal")
        expected = (
            "ACK" if phase == "seed" else "NONE" if phase == "isolated" else receipt["marker"]
        )
        protocol.require(outcome.get("output", "").strip() == expected, "context_answer")
        turn["session_id"] = outcome["session_id"]
        if phase != "seed":
            same = outcome["session_id"] == receipt["turns"]["seed"]["session_id"]
            protocol.require(same is (phase != "isolated"), "session_identity")
        turn["validated"] = True
        turn["usage"] = outcome.get("usage")
        protocol.save_receipt(receipt)
        status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=turn["body"])
        replay = json.loads(raw)
        protocol.require(
            status == 202 and replay.get("run_id") == run_id and replay.get("replayed") is True,
            "same_key_replay",
        )
        return {
            "phase": phase,
            "real_model": True,
            "status": outcome["status"],
            "exact_answer_matches": True,
            "session_identity_matches": True,
            "same_key_replay": True,
            "usage": turn["usage"],
            "qualification_status": "UNVERIFIED",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["seed", "recall", "isolated", "restart"])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.phase)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "probe_error"
        raise SystemExit(json.dumps({"error": "SESSION_PROBE_FAILED", "stage": stage})) from None
