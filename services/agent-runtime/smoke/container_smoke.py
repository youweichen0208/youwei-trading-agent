#!/usr/bin/env python3
"""Container smoke for the agent-runtime headless image (S07).

Runs INSIDE the built image (or any env with the agent-runtime venv + the
pinned Hermes checkout on PYTHONPATH). It starts a local mock OpenAI-compatible
gateway (SSE streaming), then drives the REAL `youwei-agent-runtime
research-once` entrypoint as a subprocess for the happy path and four
authorization-rejection paths. No real model, no cost.

    # inside the image (entrypoint overridden):
    docker run --rm --network host --entrypoint python \
        -v "$PWD/smoke:/smoke:ro" <image> /smoke/container_smoke.py \
        --secret <32+ char capability secret>

The capability secret is passed only via the environment to the subprocess
(YOUWEI_CAPABILITY_SECRET), never on the command line or in the request body.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from youwei_contracts.capability import sign_capability
from youwei_contracts.research import FrozenEvidence

from mock_gateway import Handler, HTTPServer


def make_evidence(tenant_id: uuid.UUID) -> FrozenEvidence:
    sec = str(uuid.uuid4())
    bars = [
        {
            "security_id": sec,
            "trade_date": f"2026-09-{22 + i:02d}",
            "close": 100.0 + i,
            "volume": 1000,
            "provenance": {"raw_object_id": str(uuid.uuid4())},
        }
        for i in range(3)
    ]
    content_sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return FrozenEvidence(
        run_id=uuid.uuid4(),
        tenant_id=tenant_id,
        case={
            "case_id": str(uuid.uuid4()),
            "security_id": sec,
            "benchmark_security_id": str(uuid.uuid4()),
            "horizon_td": 20,
            "target_spec_id": "target-spec-v1",
            "target_spec_sha256": "c" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00+00:00",
            "prediction_deadline_utc": "2026-09-28T13:15:00+00:00",
            "entry_at_utc": "2026-09-28T13:30:00+00:00",
            "exit_at_utc": "2026-10-23T20:00:00+00:00",
        },
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00+00:00",
            "mode": "forward",
            "content_sha256": content_sha,
            "content": bars,
            "manifest": {"code_version": "daily-bars-snapshot-v1"},
        },
        target_policy_sha256="d" * 64,
        batch_manifest={"calendar_version": "nyse-rules-v1"},
    )


def token(secret, *, tenant_id, scopes, exp, job_id=None, attempt_no=1):
    return sign_capability(
        secret,
        job_id=job_id or uuid.uuid4(),
        attempt_no=attempt_no,
        tenant_id=tenant_id,
        scopes=tuple(scopes),
        exp=exp,
    )


def run_once(secret, payload):
    """Run the research-once entrypoint once, returning (exit_code, stdout, stderr).

    Called as a subprocess of the same venv (the container ENTRYPOINT is
    overridden by the smoke runner); capability secret is injected only via
    env, mirroring how the Controller launches the process."""
    env = dict(os.environ)
    env["YOUWEI_CAPABILITY_SECRET"] = secret
    proc = subprocess.run(
        ["youwei-agent-runtime", "research-once"],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=120,
    )
    return proc.returncode, proc.stdout.decode().strip(), proc.stderr.decode()


def expect_ok(name, result):
    code, out, err = result
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        print(f"FAIL {name}: non-JSON stdout: {out[:200]!r}")
        return False
    if code != 0 or not parsed.get("ok"):
        print(f"FAIL {name}: code={code} out={out[:300]!r} err={err[:200]!r}")
        return False
    proposal = parsed.get("proposal", {})
    if proposal.get("source_status") != "produced":
        print(f"FAIL {name}: expected produced, got {proposal.get('source_status')}")
        return False
    if not proposal.get("references"):
        print(f"FAIL {name}: proposal has no resolved references")
        return False
    print(f"PASS {name}: produced p={proposal.get('p_outperform')} "
          f"refs={len(proposal['references'])} usage={parsed.get('usage', {}).get('source')}")
    return True


def expect_reject(name, result, expect_fragment):
    code, out, err = result
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        print(f"FAIL {name}: non-JSON stdout: {out[:200]!r}")
        return False
    if parsed.get("ok") is not False:
        print(f"FAIL {name}: expected ok=false, got {out[:200]!r}")
        return False
    error = parsed.get("error", "")
    if expect_fragment and expect_fragment not in error:
        print(f"FAIL {name}: error {error!r} missing fragment {expect_fragment!r}")
        return False
    print(f"PASS {name}: rejected ({error[:80]})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", required=True, help="capability secret (>=32 chars)")
    ap.add_argument("--gateway-port", type=int, default=9901)
    args = ap.parse_args()

    if len(args.secret) < 32:
        print("secret must be >= 32 characters")
        return 2

    gateway_url = f"http://127.0.0.1:{args.gateway_port}/v1"
    gateway = HTTPServer(("127.0.0.1", args.gateway_port), Handler)
    t = threading.Thread(target=gateway.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    try:
        tenant = uuid.uuid4()
        evidence = make_evidence(tenant)
        future = datetime.now(UTC) + timedelta(hours=1)
        past = datetime.now(UTC) - timedelta(hours=1)

        config = {"base_url": gateway_url, "api_key": "mock-key", "model": "mock-model"}
        base = {"evidence": evidence.model_dump(mode="json"), "config": config}

        results = []
        results.append(expect_ok(
            "valid-capability",
            run_once(args.secret, {**base, "capability_token": token(
                args.secret, tenant_id=tenant, scopes=["llm_call"], exp=future)}),
        ))
        results.append(expect_reject(
            "wrong-tenant",
            run_once(args.secret, {**base, "capability_token": token(
                args.secret, tenant_id=uuid.uuid4(), scopes=["llm_call"], exp=future)}),
            "tenant",
        ))
        results.append(expect_reject(
            "missing-scope",
            run_once(args.secret, {**base, "capability_token": token(
                args.secret, tenant_id=tenant, scopes=["snapshot_read"], exp=future)}),
            "scope",
        ))
        good = token(args.secret, tenant_id=tenant, scopes=["llm_call"], exp=future)
        bad = good[:-4] + ("AAAA" if good[-4:] != "AAAA" else "BBBB")
        results.append(expect_reject(
            "bad-signature",
            run_once(args.secret, {**base, "capability_token": bad}),
            "signature",
        ))
        results.append(expect_reject(
            "expired",
            run_once(args.secret, {**base, "capability_token": token(
                args.secret, tenant_id=tenant, scopes=["llm_call"], exp=past)}),
            "expired",
        ))

        print(f"\n{sum(results)}/{len(results)} scenarios passed")
        return 0 if all(results) else 1
    finally:
        gateway.shutdown()


if __name__ == "__main__":
    sys.exit(main())
