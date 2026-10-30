"""Observe local Hermes authentication/capability claims without submitting any run."""

import argparse
import ipaddress
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

LIMIT = 65536
DEADLINE_SECONDS = 10
FLAGS = (
    "run_submission",
    "run_status",
    "run_events_sse",
    "run_stop",
    "tool_progress_events",
    "run_unknown_resolution",
)
ENDPOINTS = {
    "runs": {"method": "POST", "path": "/v1/runs"},
    "run_status": {"method": "GET", "path": "/v1/runs/{run_id}"},
    "run_events": {"method": "GET", "path": "/v1/runs/{run_id}/events"},
    "run_stop": {"method": "POST", "path": "/v1/runs/{run_id}/stop"},
}


def probe(origin: str, token: str) -> dict:
    url = urlsplit(origin)
    if (
        url.scheme not in ("http", "https")
        or not ipaddress.ip_address(url.hostname or "").is_loopback
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("A loopback HTTP(S) origin is required")
    if not 32 <= len(token) <= 512 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Invalid probe token")
    statuses = []
    body = None
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        for credential in (None, "invalid-" + secrets.token_urlsafe(32), token):
            headers = {"Accept-Encoding": "identity"}
            if credential is not None:
                headers["Authorization"] = "Bearer " + credential
            started = time.monotonic()
            with client.stream(
                "GET", origin.rstrip("/") + "/v1/capabilities", headers=headers
            ) as res:
                statuses.append(res.status_code)
                expected = 200 if credential == token else 401
                if res.status_code != expected:
                    raise ValueError("Authentication/capabilities response rejected")
                if expected == 200:
                    if res.headers.get("content-encoding", "identity") != "identity":
                        raise ValueError("Capability response must be uncompressed")
                    if res.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise ValueError("Expected JSON capability response")
                    chunks = bytearray()
                    for chunk in res.iter_raw():
                        chunks.extend(chunk)
                        if len(chunks) > LIMIT or time.monotonic() - started > DEADLINE_SECONDS:
                            raise ValueError("Capability response exceeded bounds")
                    body = json.loads(chunks)
    if not isinstance(body, dict) or body.get("object") != "hermes.api_server.capabilities":
        raise ValueError("Unexpected capability document")
    features = body.get("features", {})
    endpoints = body.get("endpoints", {})
    if not isinstance(features, dict) or not isinstance(endpoints, dict):
        raise ValueError("Invalid capability structure")
    idempotency = features.get("runs_idempotency", {})
    claims = {name: features.get(name) is True for name in FLAGS}
    claims["durable_idempotency"] = (
        isinstance(idempotency, dict)
        and idempotency.get("supported") is True
        and idempotency.get("durable") is True
    )
    claims["expected_endpoints"] = all(endpoints.get(k) == v for k, v in ENDPOINTS.items())
    claims["bearer_required"] = body.get("auth") == {"type": "bearer", "required": True}
    claims["session_continuity_header"] = (
        features.get("session_continuity_header") == "X-Hermes-Session-Id"
    )
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "authentication_statuses": statuses,
        "capability_claims": claims,
        "required_claims_present": all(claims.values()),
        "capabilities": body,
        "runtime_revision_verified": False,
        "integration_status": "UNVERIFIED",
        "model_requests_sent": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.token_file.stat().st_mode & 0o077:
        raise ValueError("Token file permissions must be private")
    with args.token_file.open() as source:
        token = source.read(514).strip()
    result = probe(args.origin, token)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "capabilities"}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Hermes probe failed; no runtime approval issued") from None
