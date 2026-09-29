"""Offline tests for the Hermes runtime bridge's pure-logic surface.

parse_proposal is a wire decoder. run_research is exercised with only the
external Hermes SDK replaced; the brief, parsing and reference checks are real.
"""

import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from youwei_contracts.capability import sign_capability
from youwei_agent_runtime.runtime import parse_proposal


@pytest.fixture
def evidence():
    from youwei_contracts.research import FrozenEvidence

    rows = [{"security_id": "stock-a", "trade_date": "2026-09-25", "close": "103"}]
    return FrozenEvidence(
        run_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        case={
            "case_id": uuid.uuid4(), "security_id": uuid.uuid4(),
            "benchmark_security_id": uuid.uuid4(), "horizon_td": 20,
            "target_spec_id": "target-spec-v1", "target_spec_sha256": "a" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00Z",
            "prediction_deadline_utc": "2026-09-28T13:15:00Z",
            "entry_at_utc": "2026-09-28T13:30:00Z", "exit_at_utc": "2026-10-23T20:00:00Z",
        },
        evidence={
            "snapshot_id": uuid.uuid4(), "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00Z", "mode": "forward",
            "content_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "content": rows, "manifest": {},
        },
        target_policy_sha256="a" * 64, batch_manifest={},
    )


def install_external_agent(monkeypatch, response):
    class FakeAIAgent:
        def __init__(self, **kwargs):
            pass

        def chat(self, message):
            return response(message)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=FakeAIAgent))


def _payload(**overrides):
    base = {
        "source_status": "produced",
        "p_outperform": 0.6,
        "expected_excess_return": 0.02,
        "references": [{"kind": "evidence", "locator": "row-0"}],
        "warnings": [],
        "missing": [],
        "quantitative_basis": "momentum",
        "model": {"model_version": "m1", "provider": "p1"},
    }
    base.update(overrides)
    return base


def test_parse_plain_json():
    run_id = uuid.uuid4()
    case_id = uuid.uuid4()
    p = parse_proposal(json.dumps(_payload()), run_id=run_id, case_id=case_id)
    assert p.source_status == "produced"
    assert p.p_outperform == 0.6
    assert p.run_id == run_id
    assert p.case_id == case_id


def test_parse_strips_markdown_fence():
    raw = "```json\n" + json.dumps(_payload()) + "\n```"
    p = parse_proposal(raw, run_id=uuid.uuid4(), case_id=uuid.uuid4())
    assert p.source_status == "produced"


def test_parse_extracts_embedded_object():
    raw = "Here is my answer: " + json.dumps(_payload()) + " done."
    p = parse_proposal(raw, run_id=uuid.uuid4(), case_id=uuid.uuid4())
    assert p.p_outperform == 0.6


def test_parse_rejects_invalid_values():
    with pytest.raises(Exception):
        parse_proposal(
            json.dumps(_payload(p_outperform=1.5)),
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
        )


def test_parse_rejects_non_json_garbage():
    with pytest.raises(ValueError):
        parse_proposal("no json here at all", run_id=uuid.uuid4(), case_id=uuid.uuid4())


@pytest.mark.parametrize("locator", [
    "row-0", "prices rose yesterday",
    "snapshot:00000000-0000-0000-0000-000000000099/rows/0",
])
def test_research_does_not_return_a_proposal_with_unresolvable_citations(monkeypatch, evidence, locator):
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    install_external_agent(monkeypatch, lambda _: json.dumps(_payload(
        references=[{"kind": "evidence", "locator": locator}]
    )))
    with pytest.raises(ValueError, match="reference"):
        asyncio.run(run_research(evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        )))


def test_research_returns_citations_to_the_rows_actually_supplied(monkeypatch, evidence):
    from youwei_contracts.research import resolve_reference
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(references=[{"kind": "evidence", "locator": record["locator"]}]))

    install_external_agent(monkeypatch, respond)
    proposal = asyncio.run(run_research(evidence, ResearchConfig(
        base_url="http://unused.invalid", api_key="unused", model="external-test-double"
    )))
    assert proposal.run_id == evidence.run_id
    assert proposal.case_id == evidence.case.case_id
    assert resolve_reference(evidence, proposal.references[0])["close"] == "103"


def test_research_sets_tool_context_for_handlers_during_turn(monkeypatch, evidence):
    """When the Controller's capability is supplied, run_research carries it
    in the tool contextvar so a platform tool handler (snapshot_manifest)
    can read the frozen evidence and re-verify the grant during chat()."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research
    from youwei_agent_runtime.tools import snapshot_manifest_handler

    token = sign_capability(
        "secret", job_id=uuid.uuid4(), attempt_no=1, tenant_id=evidence.tenant_id,
        scopes=("llm_call",),
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    seen = {}

    def respond(brief):
        # Inside the turn, the handler must be able to read the run context.
        manifest = json.loads(snapshot_manifest_handler({}))
        seen["snapshot_id"] = manifest["snapshot_id"]
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    install_external_agent(monkeypatch, respond)
    asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
        capability_token=token, capability_secret="secret",
    ))
    # the handler reported the run's own frozen snapshot, proving the context
    # was correctly threaded through the thread hop to chat().
    assert seen["snapshot_id"] == str(evidence.evidence.snapshot_id)


def test_research_clears_tool_context_after_turn(monkeypatch, evidence):
    from youwei_agent_runtime.runtime import ResearchConfig, run_research
    from youwei_agent_runtime.tools import ToolAuthorizationError, current_tool_context

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    install_external_agent(monkeypatch, respond)
    asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
    ))
    # no context leaks past the turn, whether or not a token was supplied.
    with pytest.raises(ToolAuthorizationError):
        current_tool_context()
