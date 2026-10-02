#!/usr/bin/env python3
"""S07 real-gateway end-to-end: research container -> LiteLLM -> Volcengine.

Runs on sg-prod with the sandbox-runner (Python 3.13) venv. Same shape as
research_e2e.py (the S07m-3 mock-gateway smoke), but pointed at the REAL
deployed LiteLLM gateway with a REAL model, so the full chain is exercised:
Runner `docker run` -> agent-runtime container (Ed25519 grant verified,
youwei-research internal network) -> LiteLLM -> Volcengine Anthropic-compatible
endpoint.

The evidence is SYNTHETIC (deterministic fake bars; no research data). Phase 1B
data-forwarding authorization is NOT assumed by this test: it verifies the
plumbing (wire contract, streaming, tool surface, usage observation), not a
research role. Model choice here is plumbing verification, not research-role
selection (that follows Trial registration + release approval).

Environment (all read, never printed):
  YOUWEI_GATEWAY_API_KEY   REQUIRED non-empty. The restricted research virtual
                           key. Injected by the Runner into the container via
                           its own env (research.py `_gateway_credential`);
                           this script never reads or echoes it.
  YOUWEI_GATEWAY_URL       Default http://litellm:4000/v1 (Docker DNS alias on
                           the youwei-research network).
  YOUWEI_RESEARCH_MODEL    Default glm-5.3.
  YOUWEI_RESEARCH_IMAGE    Default: the published GHCR digest
                           ghcr.io/youweichen0208/youwei-agent-runtime@sha256:998f060e...
  YOUWEI_RESEARCH_NETWORK  Default youwei-research (internal, gateway egress only).
  YOUWEI_RESEARCH_TIMEOUT  Runner wall-clock timeout seconds. Default 240.
                           Set LOW (e.g. 12) with YOUWEI_EXPECT_TIMEOUT=1 to
                           exercise the Runner kill path (chain-level cancel).
  YOUWEI_EXPECT_TIMEOUT    "1" -> a ResearchExecutionError timeout is the
                           EXPECTED outcome; print RUNNER_TIMEOUT_OBSERVED and
                           exit 0 instead of failing.

Exit 0 = chain verdict OK (proposal produced or explicitly unavailable);
exit 1 = infrastructure/contract failure.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
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
from youwei_runner.research import ResearchRunConfig, execute_research_request

EXEC_CONFIG_VERSION = "research-exec-v1"
AGENT_RUNTIME_IMAGE = (
    # Verified against the real gateway 2026-10-02 (brief response-format
    # contract + config-injected model attribution + explicit output cap)
    # and republished the same day: registry digest == local manifest digest.
    "ghcr.io/youweichen0208/youwei-agent-runtime@sha256:"
    "bca0a5b9cecf5fdc2c1adfa4ac44dbe76a30be040f688f0ddf0cdd5ed9631f8a"
)


def env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def make_evidence() -> FrozenEvidence:
    """SYNTHETIC evidence: 10 deterministic fake bars, no research data."""
    tenant = uuid.uuid4()
    sec = str(uuid.uuid4())
    bars = [
        {
            "security_id": sec,
            "trade_date": f"2026-09-{17 + i:02d}",
            "close": round(100.0 + i * 1.5, 2),
            "volume": 1000 + 10 * i,
            "provenance": {"raw_object_id": str(uuid.uuid4())},
        }
        for i in range(10)
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
            "manifest": {
                "code_version": "daily-bars-snapshot-v1",
                "row_count": len(bars),
                "coverage": {sec: {"rows": len(bars)}},
            },
        },
        target_policy_sha256="d" * 64,
        batch_manifest={"calendar_version": "nyse-rules-v1"},
    )


def main() -> int:
    gateway_url = env("YOUWEI_GATEWAY_URL", "http://litellm:4000/v1")
    model = env("YOUWEI_RESEARCH_MODEL", "glm-5.3")
    image = env("YOUWEI_RESEARCH_IMAGE", AGENT_RUNTIME_IMAGE)
    network = env("YOUWEI_RESEARCH_NETWORK", "youwei-research")
    timeout_s = float(env("YOUWEI_RESEARCH_TIMEOUT", "240"))
    expect_timeout = env("YOUWEI_EXPECT_TIMEOUT", "") == "1"

    if not os.environ.get("YOUWEI_GATEWAY_API_KEY", "").strip():
        print("FATAL: YOUWEI_GATEWAY_API_KEY must be set (non-empty)", file=sys.stderr)
        return 1

    # Test-only keypair: production research signing keys are a Phase 1B
    # deployment item; this verifies the Ed25519 verify path, not key custody.
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
        config=ResearchRuntimeConfig(model=model),
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
        exp=datetime.now(UTC) + timedelta(minutes=10),
    )

    config = ResearchRunConfig(
        image=image,
        gateway_url=gateway_url,
        gateway_host=gateway_url.split("//", 1)[1],
        public_keys={kid: pub},
        exec_config_version=EXEC_CONFIG_VERSION,
        timeout_seconds=timeout_s,
        network=network,
    )

    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    print(
        f"start={started_at.isoformat()} invocation_id={invocation_id} "
        f"evidence_sha256={evidence_sha256[:16]}... model={model} "
        f"gateway={gateway_url} timeout={timeout_s}s"
    )
    try:
        result = asyncio.run(
            execute_research_request(request, config, capability_token=runtime_token)
        )
    except Exception as exc:  # ResearchExecutionError: timeout / docker fault
        elapsed = time.monotonic() - t0
        print(f"runner_error after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
        if expect_timeout and "exceeded" in str(exc):
            print(
                "RUNNER_TIMEOUT_OBSERVED "
                "(container killed mid-turn; check gateway logs/spend for the "
                "aborted stream — this is the chain-level cancel path)"
            )
            return 0
        return 1
    elapsed = time.monotonic() - t0

    print(f"elapsed={elapsed:.1f}s ok={result.ok} exit_code={result.exit_code}")
    print(f"image_digest={result.image_digest}")
    if result.proposal is not None:
        p = result.proposal
        print(
            f"proposal: source_status={p.source_status} p_outperform={p.p_outperform} "
            f"refs={len(p.references)}"
        )
        if p.source_status == "unavailable":
            print(f"reason: {p.reason}")
        print(f"warnings: {p.warnings}")
        print(f"usage: {json.dumps(result.usage, sort_keys=True)}")
    else:
        print(f"error: {result.error}")
        return 1

    chain_ok = result.ok and result.proposal is not None
    print("CHAIN OK" if chain_ok else "CHAIN FAILED")
    return 0 if chain_ok else 1


if __name__ == "__main__":
    sys.exit(main())
