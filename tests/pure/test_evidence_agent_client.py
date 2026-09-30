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
        {
            "ok": True,
            "proposal": _proposal(run_id, case["id"]).model_dump(mode="json"),
            "usage": {
                "source": "session_delta", "scope": "chat_turn", "complete": True,
                "incomplete_reasons": [], "prompt_tokens": 10,
                "completion_tokens": 20, "total_tokens": 30,
                "input_tokens": 8, "output_tokens": 20,
                "cache_read_tokens": 1, "cache_write_tokens": 1,
                "reasoning_tokens": 0, "api_calls": 2,
            },
        },
        sort_keys=True, separators=(",", ":"),
    )
    decoded = decode_result(result)
    assert decoded.proposal.source_status == "produced"
    assert decoded.proposal.p_outperform == 0.6
    # usage carried verbatim across the wire with its labeling intact.
    assert decoded.usage["source"] == "session_delta"
    assert decoded.usage["complete"] is True
    assert decoded.usage["api_calls"] == 2


def test_decode_result_defaults_usage_when_wire_omits_it():
    """An older proposal-only runtime yields a synthetic unavailable report,
    never a fabricated zero (back-compat with pre-usage runtimes)."""
    run_id = uuid.uuid4()
    case_id = uuid.uuid4()
    result = json.dumps(
        {"ok": True, "proposal": _proposal(run_id, case_id).model_dump(mode="json")},
        sort_keys=True, separators=(",", ":"),
    )
    decoded = decode_result(result)
    assert decoded.proposal.source_status == "produced"
    assert decoded.usage["source"] == "unavailable"
    assert decoded.usage["complete"] is False
    assert "usage_not_reported" in decoded.usage["incomplete_reasons"]


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


class _HangingProcess:
    """A subprocess whose communicate() never returns (e.g. a stuck
    Hermes turn). kill() marks it killed; wait() resolves after kill."""

    def __init__(self):
        self.returncode = None
        self.killed = False

    async def communicate(self, data: bytes):
        self.received = data
        # hang until cancelled by wait_for timeout or an outer cancel
        await asyncio.Event().wait()

    def kill(self):
        self.killed = True
        self.returncode = -9  # SIGKILL

    async def wait(self):
        return self.returncode


def _invocation():
    sec = str(uuid.uuid4())
    snap = _snapshot([{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}])
    case = _case(security_id=uuid.UUID(sec))
    evidence = build_frozen_evidence(
        run_id=uuid.uuid4(), tenant_id=uuid.uuid4(), case=case, snapshot=snap, batch_manifest={},
    )
    return ResearchInvocation(
        capability_token="ywc_dummy", evidence=evidence,
        config={"base_url": "x", "api_key": "k", "model": "m"},
    )


def test_run_agent_research_kills_subprocess_on_timeout():
    proc = _HangingProcess()

    async def process_factory():
        return proc

    with pytest.raises(AgentRuntimeError, match="exceeded"):
        asyncio.run(
            run_agent_research(
                _invocation(), process_factory=process_factory, timeout_seconds=0.01
            )
        )
    assert proc.killed is True


def test_run_agent_research_kills_subprocess_on_cancel():
    proc = _HangingProcess()

    async def process_factory():
        return proc

    async def drive():
        task = asyncio.create_task(
            run_agent_research(
                _invocation(), process_factory=process_factory, timeout_seconds=60.0
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    assert proc.killed is True


def test_run_agent_research_does_not_kill_on_normal_completion():
    proc = _FakeProcess(b'{"ok": false, "error": "E: boom"}', returncode=0)

    async def process_factory():
        return proc

    with pytest.raises(AgentRuntimeError, match="agent-runtime error"):
        asyncio.run(
            run_agent_research(
                _invocation(), process_factory=process_factory, timeout_seconds=5.0
            )
        )
    assert proc.killed is False


# --- S07j: Controller-side binding check (authorization, pure logic) -------


def _echo_proc_for(proposal_run_id, proposal_case_id):
    """A fake subprocess answering a proposal with explicit run/case ids
    (simulating a malicious or buggy runtime echoing the wrong binding)."""

    class _P:
        def __init__(self):
            self.returncode = 0

        async def communicate(self, data: bytes):
            proposal = _proposal(proposal_run_id, proposal_case_id)
            line = json.dumps(
                {"ok": True, "proposal": proposal.model_dump(mode="json")},
                sort_keys=True, separators=(",", ":"),
            ).encode()
            return line, b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    return _P


def _fetch_for(case, snapshot, *, run_id=None):
    run_id = run_id or uuid.uuid4()
    return make_phase1b_llm_fetcher(
        run_id=run_id,
        tenant_id=uuid.uuid4(),
        snapshot=snapshot,
        batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=None, timeout_seconds=30.0,
        ),
    ), run_id


def test_fetcher_rejects_proposal_bound_to_wrong_run():
    sec = str(uuid.uuid4())
    snap = _snapshot([{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}])
    case = _case(security_id=uuid.UUID(sec))
    fetch, run_id = _fetch_for(case, snap)
    wrong_run = uuid.uuid4()

    async def process_factory():
        return _echo_proc_for(wrong_run, case["id"])()

    fetch_with_proc = make_phase1b_llm_fetcher(
        run_id=run_id, tenant_id=uuid.uuid4(), snapshot=snap, batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=process_factory, timeout_seconds=30.0,
        ),
    )
    with pytest.raises(AgentRuntimeError, match="run_id/case_id"):
        asyncio.run(fetch_with_proc(case, []))


def test_fetcher_rejects_proposal_bound_to_wrong_case():
    sec = str(uuid.uuid4())
    snap = _snapshot([{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}])
    case = _case(security_id=uuid.UUID(sec))
    fetch, run_id = _fetch_for(case, snap)
    wrong_case = uuid.uuid4()

    async def process_factory():
        return _echo_proc_for(run_id, wrong_case)()

    fetch_with_proc = make_phase1b_llm_fetcher(
        run_id=run_id, tenant_id=uuid.uuid4(), snapshot=snap, batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=process_factory, timeout_seconds=30.0,
        ),
    )
    with pytest.raises(AgentRuntimeError, match="run_id/case_id"):
        asyncio.run(fetch_with_proc(case, []))


def test_decode_result_rejects_incompatible_proposal_shape():
    """A proposal missing the produced-value discipline must not cross the
    boundary as a valid result (contract compatibility, S07j)."""
    # produced without p_outperform -> ResearchProposal validation fails
    bad = json.dumps({
        "ok": True,
        "proposal": {
            "contract_version": "research-v1",
            "run_id": str(uuid.uuid4()),
            "case_id": str(uuid.uuid4()),
            "source_status": "produced",
            "model": {"model_version": "m", "provider": "p"},
        },
    })
    with pytest.raises(Exception):  # pydantic ValidationError (value discipline)
        decode_result(bad)


def test_decode_result_rejects_non_json_result():
    with pytest.raises(AgentRuntimeError, match="non-JSON"):
        decode_result("not json at all")


def test_fetcher_passes_usage_to_sink():
    """The fetched turn's usage report is handed to the optional usage_sink
    verbatim (source/scope/complete + counters) for observability."""
    run_id = uuid.uuid4()
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(security_id=uuid.UUID(sec))
    proposal = _proposal(run_id, case["id"])
    usage_payload = {
        "source": "session_delta", "scope": "chat_turn", "complete": True,
        "incomplete_reasons": [], "prompt_tokens": 10,
        "completion_tokens": 20, "total_tokens": 30,
        "input_tokens": 8, "output_tokens": 20,
        "cache_read_tokens": 1, "cache_write_tokens": 1,
        "reasoning_tokens": 0, "api_calls": 2,
    }
    result_line = json.dumps(
        {
            "ok": True,
            "proposal": proposal.model_dump(mode="json"),
            "usage": usage_payload,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()

    async def process_factory():
        return _FakeProcess(result_line)

    captured = []
    fetch = make_phase1b_llm_fetcher(
        run_id=run_id,
        tenant_id=uuid.uuid4(),
        snapshot=snap,
        batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=process_factory, timeout_seconds=30.0,
        ),
        usage_sink=captured.append,
    )
    got = asyncio.run(fetch(case, bars))
    assert got.source_status == "produced"
    assert len(captured) == 1
    assert captured[0] == usage_payload


def test_fetcher_without_sink_drops_usage_but_returns_proposal():
    """Without a usage_sink the fetcher still returns the proposal (usage is
    dropped, not an error)."""
    run_id = uuid.uuid4()
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    snap = _snapshot(bars)
    case = _case(security_id=uuid.UUID(sec))
    proposal = _proposal(run_id, case["id"])
    result_line = json.dumps(
        {
            "ok": True,
            "proposal": proposal.model_dump(mode="json"),
            "usage": {"source": "session_delta", "scope": "chat_turn", "complete": True},
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()

    async def process_factory():
        return _FakeProcess(result_line)

    fetch = make_phase1b_llm_fetcher(
        run_id=run_id, tenant_id=uuid.uuid4(), snapshot=snap, batch_manifest={},
        capability_token="ywc_dummy",
        agent_runtime=AgentRuntimeConfig(
            research_config={}, process_factory=process_factory, timeout_seconds=30.0,
        ),
    )
    got = asyncio.run(fetch(case, bars))
    assert got.source_status == "produced"
