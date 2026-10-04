#!/usr/bin/env python3
"""S07m-3c end-to-end: Controller research link over the Runner (real Docker).

Runs on sg-prod with the sandbox-runner (Python 3.13) environment. It drives
the REAL `execute_research_request` -> `run_research_container` path: the
Runner launches the research image with `docker run -i`, forwards a single
research invocation over stdin/stdout, and the container verifies the
Controller's Ed25519 grant, calls the mock gateway (the ONLY allowed egress on
the internal `youwei-research` network), and returns a produced proposal.

The mock gateway must already be running on the internal network (see the
S07m-3c checklist). No real model, no cost.

Run with a non-empty `YOUWEI_GATEWAY_API_KEY` (the Runner injects it as the
restricted gateway credential; the mock gateway does not verify it, but Hermes
requires a non-empty key to consider the provider configured).
"""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from youwei_contracts.agent_runtime import (
    ResearchInvocationRequest,
    ResearchRuntimeConfig,
)
from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
)
from youwei_runner.research import (
    ResearchRunConfig,
    execute_research_request,
)

EXEC_CONFIG_VERSION = "research-exec-v1"
# The mock gateway container on the youwei-research network. The subnet is
# NOT stable across `docker network create` (S07m used 172.19.0.0/16; a 2026-
# 10-02 recreation got 172.27.0.0/16) — override with YOUWEI_GATEWAY_URL or
# resolve the mock container's current IP instead of trusting this default.
GATEWAY_URL = os.environ.get("YOUWEI_GATEWAY_URL", "http://172.19.0.2:9901/v1")
IMAGE = os.environ.get(
    "YOUWEI_RESEARCH_IMAGE",
    "youwei/agent-runtime:dev",
)


def make_evidence() -> FrozenEvidence:
    tenant = uuid.uuid4()
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
        tenant_id=tenant,
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
        quant={
            "model_version": "quant-momentum-v0",
            "source_status": "produced",
            "p_outperform": 0.55,
            "expected_excess_return": 0.01,
        },
    )


def main():
    priv, pub = generate_research_keypair()
    kid = public_key_thumbprint(pub)[:16]

    evidence = make_evidence()
    canonical = json.dumps(
        evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    evidence_sha256 = hashlib.sha256(canonical).hexdigest()

    invocation_id = uuid.uuid4()
    request = ResearchInvocationRequest(
        invocation_id=invocation_id,
        tenant_id=evidence.tenant_id,
        run_id=evidence.run_id,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=evidence.case.case_id,
        evidence=evidence,
        evidence_sha256=evidence_sha256,
        exec_config_version=EXEC_CONFIG_VERSION,
        config=ResearchRuntimeConfig(model="mock-model"),
    )

    runtime_token = sign_research_token(
        priv,
        kid=kid,
        aud=AUD_RUNTIME_RESEARCH,
        invocation_id=invocation_id,
        tenant_id=evidence.tenant_id,
        run_id=evidence.run_id,
        job_id=request.job_id,
        attempt_no=1,
        case_id=evidence.case.case_id,
        evidence_sha256=evidence_sha256,
        exec_config_version=EXEC_CONFIG_VERSION,
        scopes=(SCOPE_RESEARCH_RUN,),
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )

    config = ResearchRunConfig(
        image=IMAGE,
        gateway_url=GATEWAY_URL,
        gateway_host=GATEWAY_URL.split("//", 1)[1],
        public_keys={kid: pub},
        exec_config_version=EXEC_CONFIG_VERSION,
        timeout_seconds=120.0,
        network="youwei-research",
    )

    result = asyncio.run(
        execute_research_request(request, config, capability_token=runtime_token)
    )

    print(f"ok={result.ok} exit_code={result.exit_code} image_digest={result.image_digest}")
    if result.proposal is not None:
        p = result.proposal
        print(f"proposal: source_status={p.source_status} p_outperform={p.p_outperform} "
              f"refs={len(p.references)} case_id={p.case_id}")
        print(f"usage: {result.usage}")
    else:
        print(f"error: {result.error}")
    if result.error:
        print(f"error detail: {result.error}")

    ok = result.ok and result.proposal is not None and result.proposal.source_status == "produced"
    print("END_TO_END OK" if ok else "END_TO_END FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
