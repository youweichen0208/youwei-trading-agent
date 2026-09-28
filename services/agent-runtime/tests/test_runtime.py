"""Offline tests for the Hermes runtime bridge's pure-logic surface.

parse_proposal does not import Hermes (deferred), so it is testable here
without the pinned checkout or a gateway. run_research / make_agent require
the SG host + gateway and are exercised by the end-to-end smoke, not here.
"""

import json
import uuid

import pytest

from youwei_agent_runtime.runtime import parse_proposal


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
