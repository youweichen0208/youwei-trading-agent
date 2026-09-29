"""End-to-end smoke: FrozenEvidence -> brief -> AIAgent.chat(mock) -> proposal.

No real model, no cost. Exercises the exact runtime path the Controller will
drive in Phase 1B, but against the local mock gateway. Run on the SG host:

    # terminal 1
    $PY -m youwei_agent_runtime.smoke.mock_gateway 9901
    # terminal 2
    PYTHONPATH=<hermes-checkout>:<contracts-src>:<agent-runtime-src> \
        $PY youwei_agent_runtime/smoke/smoke_e2e.py

``$PY`` is the pinned Hermes Python 3.14 venv interpreter.
"""

import hashlib
import json
import os
import sys
import uuid

# Resolve the three required sources from the environment when present;
# defaults keep the SG-host layout working without extra config.
_HERMES = os.environ.get("HERMES_CHECKOUT", "/root/s01-verify/hermes314")
_CONTRACTS = os.environ.get(
    "CONTRACTS_SRC", "/root/youwei-agent-runtime/contracts-src"
)
_AGENT_SRC = os.environ.get(
    "AGENT_RUNTIME_SRC", "/root/youwei-agent-runtime/src"
)
for _p in (_HERMES, _CONTRACTS, _AGENT_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from youwei_agent_runtime.adapter import ISOLATION_KWARGS, build_research_brief  # noqa: E402
from youwei_agent_runtime.runtime import (  # noqa: E402
    ResearchConfig,
    _register_research_tools,
    make_agent,
    parse_proposal,
)
from youwei_contracts.research import FrozenEvidence, validate_proposal_references  # noqa: E402


def make_evidence() -> FrozenEvidence:
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
        tenant_id=uuid.uuid4(),
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


def main() -> None:
    evidence = make_evidence()
    config = ResearchConfig(
        base_url="http://127.0.0.1:9901/v1",
        api_key="mock-key",
        model="mock-model",
    )
    print("=== isolation kwargs ===")
    print(ISOLATION_KWARGS)

    print("\n=== building agent ===")
    # Register the platform research tools into Hermes's global registry before
    # constructing the agent, exactly as research-once does (main.py). Without
    # this, enabled_toolsets=["youwei-research"] would select an empty toolset.
    _register_research_tools()
    agent = make_agent(config)
    print("agent memory store:", agent._memory_store)
    print("agent valid tools:", sorted(agent.valid_tool_names))

    print("\n=== research brief (first 400 chars) ===")
    brief = build_research_brief(evidence)
    print(brief[:400])

    print("\n=== chat() ===")
    raw = agent.chat(brief)
    print("raw response:", raw[:300])

    print("\n=== parse proposal ===")
    proposal = parse_proposal(
        raw, run_id=evidence.run_id, case_id=evidence.case.case_id
    )
    rows = validate_proposal_references(evidence, proposal)
    print("source_status:", proposal.source_status)
    print("p_outperform:", proposal.p_outperform)
    print("expected_excess_return:", proposal.expected_excess_return)
    print("model:", proposal.model)
    print("references:", proposal.references)
    print("resolved evidence rows:", len(rows))
    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
