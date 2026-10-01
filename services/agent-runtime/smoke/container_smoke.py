#!/usr/bin/env python3
"""Container smoke for the agent-runtime headless image (S07m).

Runs INSIDE the built image (or any env with the agent-runtime venv + the
pinned Hermes checkout on PYTHONPATH). It starts a local mock OpenAI-compatible
gateway (SSE streaming), then drives the REAL `youwei-agent-runtime
research-once` entrypoint as a subprocess for the happy path and four
authorization-rejection paths. No real model, no cost.

    # inside the image (entrypoint overridden):
    docker run --rm --entrypoint python \
        -v "$PWD/smoke:/smoke:ro" <image> /smoke/container_smoke.py \
        --private-key "$(cat /path/research-key.pem)"

The research link uses Ed25519 (S07m): the smoke runner HOLDS the private key
to SIGN grants, but it passes only the PUBLIC key (kid -> PEM) to the
research-once subprocess via YOUWEI_RESEARCH_PUBLIC_KEYS. This proves the
research container can VERIFY a grant but never sign one. No secret is shared
with the container; nothing appears on the command line or in the request body.
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

from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
)

from mock_gateway import Handler, HTTPServer

EXEC_CONFIG_VERSION = "research-exec-v1"


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


def make_token(private_key_pem, *, kid, evidence, tenant_id, scopes, exp):
    """Sign one runtime-research grant bound to the evidence's run/case."""
    return sign_research_token(
        private_key_pem,
        kid=kid,
        aud=AUD_RUNTIME_RESEARCH,
        invocation_id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=evidence.run_id,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=evidence.case.case_id,
        evidence_sha256="e" * 64,
        exec_config_version=EXEC_CONFIG_VERSION,
        scopes=scopes,
        exp=exp,
    )


def run_once(public_keys_json, payload):
    """Run the research-once entrypoint once, returning (exit_code, stdout, stderr).

    Called as a subprocess of the same venv (the container ENTRYPOINT is
    overridden by the smoke runner); only the PUBLIC key map is injected via
    env, mirroring how the Runner launches the research container."""
    env = dict(os.environ)
    env["YOUWEI_RESEARCH_PUBLIC_KEYS"] = public_keys_json
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
    ap.add_argument("--private-key", default=None,
                    help="Ed25519 private key PEM (default: generate a fresh pair)")
    ap.add_argument("--public-key", default=None,
                    help="Ed25519 public key PEM to trust (default: derive from --private-key)")
    ap.add_argument("--gateway-port", type=int, default=9901)
    args = ap.parse_args()

    if args.private_key:
        private_key_pem = open(args.private_key).read()
        if args.public_key:
            public_key_pem = open(args.public_key).read()
        else:
            from cryptography.hazmat.primitives import serialization
            priv = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
            public_key_pem = priv.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()
    else:
        private_key_pem, public_key_pem = generate_research_keypair()

    kid = public_key_thumbprint(public_key_pem)[:16]
    public_keys = {kid: public_key_pem}
    public_keys_json = json.dumps(public_keys, sort_keys=True)

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

        def tok(tenant_id, scopes, exp):
            return make_token(private_key_pem, kid=kid, evidence=evidence,
                              tenant_id=tenant_id, scopes=scopes, exp=exp)

        results = []
        results.append(expect_ok(
            "valid-grant",
            run_once(public_keys_json, {**base, "capability_token": tok(
                tenant, [SCOPE_RESEARCH_RUN], future)}),
        ))
        results.append(expect_reject(
            "wrong-tenant",
            run_once(public_keys_json, {**base, "capability_token": tok(
                uuid.uuid4(), [SCOPE_RESEARCH_RUN], future)}),
            "tenant",
        ))
        results.append(expect_reject(
            "missing-scope",
            run_once(public_keys_json, {**base, "capability_token": tok(
                tenant, ["snapshot_read"], future)}),
            "scope",
        ))
        good = tok(tenant, [SCOPE_RESEARCH_RUN], future)
        bad = good[:-4] + ("AAAA" if good[-4:] != "AAAA" else "BBBB")
        results.append(expect_reject(
            "bad-signature",
            run_once(public_keys_json, {**base, "capability_token": bad}),
            "signature",
        ))
        results.append(expect_reject(
            "expired",
            run_once(public_keys_json, {**base, "capability_token": tok(
                tenant, [SCOPE_RESEARCH_RUN], past)}),
            "expired",
        ))

        print(f"\n{sum(results)}/{len(results)} scenarios passed")
        return 0 if all(results) else 1
    finally:
        gateway.shutdown()


if __name__ == "__main__":
    sys.exit(main())
