"""S07h: Controller-side evidence assembly and agent-runtime subprocess client.

These pure tests (no PostgreSQL, no Hermes checkout) pin the codec and the
FrozenEvidence assembly the Controller uses to hand one case's frozen bundle
across the agent-runtime process boundary, plus the fetch_proposal factory
that drives a (fake) subprocess and returns a validated ResearchProposal.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from youwei_contracts.research import FrozenEvidence, ResearchProposal
from youwei_core.ledger.agent_client import (
    AgentRuntimeError,
    ResearchInvocation,
    decode_result,
    encode_request,
    run_agent_research,
)
from youwei_core.ledger.evidence import (
    EvidenceAssemblyError,
    build_case_plan,
    build_frozen_evidence,
)
from youwei_core.ledger.pipeline import AgentRuntimeConfig, make_phase1b_llm_fetcher


def _case(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "security_id": uuid.uuid4(),
        "benchmark_security_id": uuid.uuid4(),
        "horizon_td": 20,
        "target_spec_id": "excess-tr-d20-v1",
        "target_spec_sha256": "a" * 64,
        "decision_cutoff_utc": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "prediction_deadline_utc": datetime(2026, 9, 28, 13, 15, tzinfo=UTC),
        "entry_at_utc": datetime(2026, 9, 28, 13, 30, tzinfo=UTC),
        "exit_at_utc": datetime(2026, 10, 23, 20, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _snapshot(content: list[dict], *, mode="forward") -> dict:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "id": str(uuid.uuid4()),
        "manifest": {
            "kind": "daily_bars",
            "query": {
                "kind": "daily_bars",
                "as_of": "2026-09-26T10:00:00+00:00",
                "mode": mode,
            },
            "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "code_version": "daily-bars-snapshot-v1",
        },
        "content": canonical,
    }


def test_build_frozen_evidence_assembles_bundle():
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(security_id=uuid.UUID(sec))
    evidence = build_frozen_evidence(
        run_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        case=case,
        snapshot=snap,
        batch_manifest={"calendar_version": "nyse-rules-v1"},
    )
    assert evidence.case.case_id == case["id"]
    assert evidence.case.horizon_td == 20
    assert evidence.target_policy_sha256 == "a" * 64
    assert evidence.evidence.snapshot_id == uuid.UUID(snap["id"])
    assert evidence.evidence.mode == "forward"
    assert evidence.evidence.content == bars
    assert evidence.batch_manifest == {"calendar_version": "nyse-rules-v1"}


def test_build_frozen_evidence_rejects_non_forward_snapshot():
    snap = _snapshot([], mode="historical_source")
    with pytest.raises(EvidenceAssemblyError, match="forward"):
        build_frozen_evidence(
            run_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
            case=_case(), snapshot=snap, batch_manifest={},
        )


def test_build_frozen_evidence_rejects_tampered_content():
    snap = _snapshot([{"security_id": "x", "trade_date": "2026-09-25", "close": 1.0}])
    snap["content"] = json.dumps([{"security_id": "y"}])  # hash no longer matches
    with pytest.raises(Exception):  # pydantic hash validation
        build_frozen_evidence(
            run_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
            case=_case(), snapshot=snap, batch_manifest={},
        )


def _proposal(run_id, case_id) -> ResearchProposal:
    return ResearchProposal(
        run_id=run_id,
        case_id=case_id,
        source_status="produced",
        p_outperform=0.6,
        expected_excess_return=0.02,
        model={"model_version": "llm-v1", "provider": "test"},
    )


def test_encode_request_and_decode_result_roundtrip():
    run_id = uuid.uuid4()
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(security_id=uuid.UUID(sec))
    evidence = build_frozen_evidence(
        run_id=run_id, tenant_id=uuid.uuid4(), case=case, snapshot=snap, batch_manifest={},
    )
    inv = ResearchInvocation(
        capability_token="ywc_dummy", evidence=evidence, config={"base_url": "x", "api_key": "k", "model": "m"},
    )
    raw = encode_request(inv)
    payload = json.loads(raw)
    assert payload["capability_token"] == "ywc_dummy"
    assert payload["evidence"]["run_id"] == str(run_id)

    result = json.dumps(
        {"ok": True, "proposal": _proposal(run_id, case["id"]).model_dump(mode="json")},
        sort_keys=True, separators=(",", ":"),
    )
    decoded = decode_result(result)
    assert decoded.source_status == "produced"
    assert decoded.p_outperform == 0.6


def test_decode_result_errors_on_non_ok():
    with pytest.raises(AgentRuntimeError, match="agent-runtime error"):
        decode_result(json.dumps({"ok": False, "error": "boom"}))


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self, data: bytes):
        self.received = data
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def test_fetch_proposal_drives_subprocess_and_returns_proposal():
    run_id = uuid.uuid4()
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(security_id=uuid.UUID(sec))
    proposal = _proposal(run_id, case["id"])
    result_line = json.dumps(
        {"ok": True, "proposal": proposal.model_dump(mode="json")},
        sort_keys=True, separators=(",", ":"),
    ).encode()

    captured = {}

    async def process_factory():
        proc = _FakeProcess(result_line)
        captured["proc"] = proc
        return proc

    fetch = make_phase1b_llm_fetcher(
        run_id=run_id,
        tenant_id=uuid.uuid4(),
        snapshot=snap,
        batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={"base_url": "x", "api_key": "k", "model": "m"},
            process_factory=process_factory,
            timeout_seconds=30.0,
        ),
    )
    got = asyncio.run(fetch(case, bars))
    assert got.source_status == "produced"
    assert got.p_outperform == 0.6
    # the subprocess received the evidence bundle + capability token
    sent = json.loads(captured["proc"].received)
    assert sent["capability_token"] == "ywc_dummy"
    assert sent["evidence"]["case"]["case_id"] == str(case["id"])


def test_fetch_proposal_raises_on_subprocess_failure():
    run_id = uuid.uuid4()
    sec = str(uuid.uuid4())
    snap = _snapshot([{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}])
    case = _case(security_id=uuid.UUID(sec))

    async def process_factory():
        return _FakeProcess(b"", stderr=b"kaboom", returncode=1)

    fetch = make_phase1b_llm_fetcher(
        run_id=run_id, tenant_id=uuid.uuid4(), snapshot=snap, batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=process_factory, timeout_seconds=30.0,
        ),
    )
    with pytest.raises(AgentRuntimeError, match="exited 1"):
        asyncio.run(fetch(case, []))
