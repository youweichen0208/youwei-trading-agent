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

from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    sign_research_token,
)
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


def install_external_agent(monkeypatch, response, *, usage=None, session_delta=None, initial=None):
    """Install a fake Hermes AIAgent.

    ``usage`` sets ``_last_turn_usage`` (the last-call dict) on the agent;
    ``initial`` sets the session counters' pre-turn values; ``session_delta``
    adds to them over the turn (may be negative to simulate a mid-turn reset).
    A fake with neither ``usage`` nor ``session_delta``/``initial`` simulates a
    checkout with no usage signal at all.
    """
    _ATTRS = {
        "prompt_tokens": "session_prompt_tokens",
        "completion_tokens": "session_completion_tokens",
        "total_tokens": "session_total_tokens",
        "input_tokens": "session_input_tokens",
        "output_tokens": "session_output_tokens",
        "cache_read_tokens": "session_cache_read_tokens",
        "cache_write_tokens": "session_cache_write_tokens",
        "reasoning_tokens": "session_reasoning_tokens",
        "api_calls": "session_api_calls",
    }
    # Session counters only exist on the fake when the caller opts in; a fake
    # with neither ``initial`` nor ``session_delta`` has NO session counters
    # (so _snapshot_session_usage returns None → fallback/unavailable path).
    _has_session = initial is not None or session_delta is not None

    class FakeAIAgent:
        def __init__(self, **kwargs):
            if _has_session:
                for attr in _ATTRS.values():
                    setattr(self, attr, 0)
                if initial:
                    for key, value in initial.items():
                        setattr(self, _ATTRS[key], value)

        def chat(self, message):
            if usage is not None:
                self._last_turn_usage = dict(usage)
            if session_delta is not None:
                for key, value in session_delta.items():
                    attr = _ATTRS[key]
                    setattr(self, attr, getattr(self, attr) + value)
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
    turn = asyncio.run(run_research(evidence, ResearchConfig(
        base_url="http://unused.invalid", api_key="unused", model="external-test-double"
    )))
    proposal = turn.proposal
    assert proposal.run_id == evidence.run_id
    assert proposal.case_id == evidence.case.case_id
    assert resolve_reference(evidence, proposal.references[0])["close"] == "103"


def test_research_sets_tool_context_for_handlers_during_turn(monkeypatch, evidence):
    """When the Controller's grant is supplied, run_research carries it
    in the tool contextvar so a platform tool handler (snapshot_manifest)
    can read the frozen evidence and re-verify the grant during chat()."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research
    from youwei_agent_runtime.tools import snapshot_manifest_handler

    priv, pub = generate_research_keypair()
    token = sign_research_token(
        priv, kid="k1", aud=AUD_RUNTIME_RESEARCH, invocation_id=uuid.uuid4(),
        tenant_id=evidence.tenant_id, run_id=evidence.run_id, job_id=uuid.uuid4(),
        attempt_no=1, case_id=evidence.case.case_id,
        evidence_sha256="e" * 64, exec_config_version="v1",
        scopes=(SCOPE_RESEARCH_RUN,), exp=datetime.now(UTC) + timedelta(minutes=5),
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
        capability_token=token, public_keys={"k1": pub},
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


def test_research_returns_session_delta_usage(monkeypatch, evidence):
    """The turn's usage is the session-counter delta (verified against the
    pinned checkout's session accounting), labeled complete when every counter
    is present and non-negative."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    install_external_agent(monkeypatch, respond, session_delta={
        "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
        "input_tokens": 8, "output_tokens": 20,
        "cache_read_tokens": 1, "cache_write_tokens": 1, "reasoning_tokens": 0,
        "api_calls": 2,
    })
    result = asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
    ))
    assert result.proposal.source_status == "produced"
    u = result.usage
    assert u.source == "session_delta"
    assert u.scope == "chat_turn"
    assert u.complete is True
    assert u.incomplete_reasons == ()
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 20
    assert u.total_tokens == 30
    assert u.input_tokens == 8
    assert u.output_tokens == 20
    assert u.cache_read_tokens == 1
    assert u.cache_write_tokens == 1
    assert u.reasoning_tokens == 0  # confirmed zero, not unknown
    assert u.api_calls == 2


def test_research_falls_back_to_last_call_when_no_session_counters(monkeypatch, evidence):
    """Without session counters (older checkout / minimal fake), the last-call
    usage is returned as a fallback explicitly marked incomplete (it only
    covers the final API call, not retries/tool-loop/auxiliary calls)."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    install_external_agent(monkeypatch, respond, usage={
        "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
    })
    result = asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
    ))
    u = result.usage
    assert u.source == "last_call_fallback"
    assert u.scope == "last_api_call"
    assert u.complete is False
    assert "last_call_scope_only" in u.incomplete_reasons
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 20
    assert u.total_tokens == 30
    assert u.api_calls is None


def test_research_returns_unavailable_without_any_usage_signal(monkeypatch, evidence):
    """A checkout/fake with no usage signal yields an explicit unavailable
    report (unknown, not a fabricated zero)."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    install_external_agent(monkeypatch, respond)  # no usage, no session_delta
    result = asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
    ))
    u = result.usage
    assert u.source == "unavailable"
    assert u.scope == "unknown"
    assert u.complete is False
    assert "no_usage_signal" in u.incomplete_reasons
    assert u.prompt_tokens is None
    assert u.api_calls is None


def test_research_marks_counter_reset_incomplete(monkeypatch, evidence):
    """A session counter that rolls backwards (mid-turn reset) makes the delta
    unreliable and forces complete=False."""
    from youwei_agent_runtime.runtime import ResearchConfig, run_research

    def respond(brief):
        record = next(json.loads(line) for line in brief.splitlines() if line.startswith('{"locator":'))
        return json.dumps(_payload(
            references=[{"kind": "evidence", "locator": record["locator"]}]
        ))

    # The agent already had 100 total tokens before the turn (e.g. a reused
    # session), then the session counters reset mid-turn so the after value
    # drops below the before value.
    install_external_agent(
        monkeypatch, respond,
        initial={"total_tokens": 100, "api_calls": 5},
        session_delta={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": -100, "api_calls": 1},
    )
    result = asyncio.run(run_research(
        evidence, ResearchConfig(
            base_url="http://unused.invalid", api_key="unused", model="external-test-double"
        ),
    ))
    u = result.usage
    assert u.complete is False
    assert "total_tokens_counter_reset" in u.incomplete_reasons
    assert u.source == "session_delta"
    assert u.scope == "chat_turn"
    # The forward counters still differenced cleanly.
    assert u.prompt_tokens == 5
    assert u.api_calls == 1
