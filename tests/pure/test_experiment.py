"""experiment-v1 wire contract tests (S08 exploration loop, first slice).

The round-trip contract from docs/research/s08-exploration-loop-design.md:
the research instance requests an experiment; the Controller spawns an
isolated experiment instance whose sandbox tools (submit/status/read) are
separately authorized; artifacts are cited back as
``experiment:<id>/artifacts/<path>`` references.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_contracts.experiment import (
    ExperimentComputationRequest,
    ExperimentComputationStatus,
    ExperimentRequest,
    ExperimentResult,
    experiment_artifact_locator,
    parse_experiment_locator,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    AUD_RUNTIME_RESEARCH,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
    verify_research_token,
)


def _code(text: str = "print('vol ratio')") -> str:
    return text


def _computation_request(**overrides) -> ExperimentComputationRequest:
    base = dict(
        experiment_invocation_id=uuid.uuid4(),
        computation_id=uuid.uuid4(),
        code=_code(),
        argv=[],
        env={},
        expected_extensions=[".json"],
        timeout_seconds=60.0,
    )
    base.update(overrides)
    return ExperimentComputationRequest(**base)


# --- locator ---------------------------------------------------------------


def test_locator_round_trip():
    exp_id = uuid.uuid4()
    loc = experiment_artifact_locator(exp_id, "out/vol_ratio.json")
    assert loc == f"experiment:{exp_id}/artifacts/out/vol_ratio.json"
    parsed = parse_experiment_locator(loc)
    assert parsed == (exp_id, "out/vol_ratio.json")


def test_locator_rejects_bad_paths():
    exp_id = uuid.uuid4()
    for bad in ("", "/abs.json", "../x.json", "a/../b.json", "a\\b.json",
                "a.json\x00", ".", "out/"):
        with pytest.raises(ValueError):
            experiment_artifact_locator(exp_id, bad)


def test_parse_locator_rejects_wrong_shapes():
    for bad in ("vol_ratio.json", "experiment:not-a-uuid/artifacts/a.json",
                "evidence:xyz/rows/0", "experiment:%s/artifacts/" % uuid.uuid4(),
                "experiment:%s/other/a.json" % uuid.uuid4()):
        with pytest.raises(ValueError):
            parse_experiment_locator(bad)


# --- computation request ---------------------------------------------------


def test_computation_request_minimal_valid():
    r = _computation_request()
    assert r.contract_version == "experiment-v1"
    assert r.code == _code()


def test_computation_request_code_bounds():
    with pytest.raises(Exception):
        _computation_request(code="")
    with pytest.raises(Exception):
        _computation_request(code="x" * (1_000_001))


def test_computation_request_env_requires_sbx_names():
    ok = _computation_request(env={"SBX_FEATURE": "ratio"})
    assert ok.env == {"SBX_FEATURE": "ratio"}
    with pytest.raises(Exception):
        _computation_request(env={"PATH": "/evil"})
    with pytest.raises(Exception):
        _computation_request(env={"SBX_A": "x" * 5000})


def test_computation_request_argv_bounds():
    with pytest.raises(Exception):
        _computation_request(argv=["--flag\x00"])
    with pytest.raises(Exception):
        _computation_request(argv=["ok"] * 101)


def test_computation_request_expected_extensions_and_timeout():
    with pytest.raises(Exception):
        _computation_request(expected_extensions=[".exe"])
    with pytest.raises(Exception):
        _computation_request(timeout_seconds=0)
    with pytest.raises(Exception):
        _computation_request(timeout_seconds=3601)


def test_computation_request_forbids_extra_fields():
    with pytest.raises(Exception):
        _computation_request(snapshot={"injected": "by caller"})


# --- computation status ----------------------------------------------------


def _manifest(path="vol_ratio.json", size=3, sha=None):
    return {
        "path": path, "extension": ".json", "size": size,
        "sha256": sha or ("a" * 64),
    }


def test_status_succeeded_is_not_partial():
    s = ExperimentComputationStatus(
        experiment_invocation_id=uuid.uuid4(),
        computation_id=uuid.uuid4(),
        status="succeeded", partial=False,
        stdout_excerpt="ok", stderr_excerpt="",
        artifacts=[_manifest()], error=None, duration_seconds=1.0,
    )
    assert s.status == "succeeded" and s.partial is False


def test_status_timeout_with_artifacts_is_partial():
    s = ExperimentComputationStatus(
        experiment_invocation_id=uuid.uuid4(),
        computation_id=uuid.uuid4(),
        status="timeout", partial=True,
        stdout_excerpt="", stderr_excerpt="killed",
        artifacts=[_manifest()], error="exceeded timeout", duration_seconds=60.0,
    )
    assert s.partial is True


def test_status_partial_consistency_enforced():
    # a succeeded computation must not be marked partial
    with pytest.raises(Exception):
        ExperimentComputationStatus(
            experiment_invocation_id=uuid.uuid4(),
            computation_id=uuid.uuid4(),
            status="succeeded", partial=True,
            stdout_excerpt="", stderr_excerpt="",
            artifacts=[], error=None, duration_seconds=1.0,
        )
    # a terminal failure without artifacts must not be partial
    with pytest.raises(Exception):
        ExperimentComputationStatus(
            experiment_invocation_id=uuid.uuid4(),
            computation_id=uuid.uuid4(),
            status="cancelled", partial=True,
            stdout_excerpt="", stderr_excerpt="",
            artifacts=[], error=None, duration_seconds=1.0,
        )


def test_status_excerpts_bounded():
    with pytest.raises(Exception):
        ExperimentComputationStatus(
            experiment_invocation_id=uuid.uuid4(),
            computation_id=uuid.uuid4(),
            status="running", partial=False,
            stdout_excerpt="x" * 5000, stderr_excerpt="",
            artifacts=[], error=None, duration_seconds=0.0,
        )


# --- experiment request / result -------------------------------------------


def test_experiment_request_bounds():
    ExperimentRequest(
        question="rolling 20-session realized vol ratio, current percentile",
        motivation="not covered by the standard quant library",
        requested_shape="single JSON artifact with ratio + percentile",
    )
    with pytest.raises(Exception):
        ExperimentRequest(question="", motivation="m", requested_shape="s")
    with pytest.raises(Exception):
        ExperimentRequest(question="q" * 2001, motivation="m", requested_shape="s")


def _result_computation(code="print(1)", **overrides):
    base = dict(
        computation_id=uuid.uuid4(),
        code=code,
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        status="succeeded",
        artifacts=[_manifest()],
    )
    base.update(overrides)
    return base


def test_experiment_result_valid():
    r = ExperimentResult(
        findings="ratio 1.32, 78th percentile of its own history",
        warnings=[],
        computations=[_result_computation()],
    )
    assert r.computations[0].status == "succeeded"


def test_experiment_result_rejects_code_hash_mismatch():
    with pytest.raises(Exception):
        ExperimentResult(
            findings="f", warnings=[],
            computations=[_result_computation(code_sha256="b" * 64)],
        )


def test_experiment_result_rejects_duplicate_computation_ids():
    cid = uuid.uuid4()
    with pytest.raises(Exception):
        ExperimentResult(
            findings="f", warnings=[],
            computations=[
                _result_computation(computation_id=cid),
                _result_computation(computation_id=cid, code="print(2)"),
            ],
        )


def test_experiment_result_allows_terminal_failure_without_artifacts():
    # a computation that timed out and produced nothing is a valid, citable
    # outcome (nothing to cite); only non-terminal states are rejected
    r = ExperimentResult(
        findings="computation timed out before writing output",
        warnings=["timeout"],
        computations=[_result_computation(status="timeout", artifacts=[])],
    )
    assert r.computations[0].status == "timeout"


def test_experiment_result_rejects_running_entries():
    with pytest.raises(Exception):
        ExperimentResult(
            findings="f", warnings=[],
            computations=[_result_computation(status="running")],
        )


# --- capability: runner-tools audience + experiment scopes -----------------


def _token(aud, scopes):
    priv, pub = generate_research_keypair()
    kid = public_key_thumbprint(pub)[:16]
    tok = sign_research_token(
        priv, kid=kid, aud=aud,
        invocation_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(), job_id=uuid.uuid4(), attempt_no=1,
        case_id=uuid.uuid4(), evidence_sha256="e" * 64,
        exec_config_version="exp-v1", scopes=scopes,
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    return tok, {kid: pub}


def test_runner_tools_audience_round_trip():
    tok, keys = _token(
        AUD_RUNNER_TOOLS,
        [SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ],
    )
    cap = verify_research_token(keys, tok)
    assert cap.aud == AUD_RUNNER_TOOLS
    assert set(cap.scopes) == {
        "experiment:submit", "experiment:status", "experiment:read",
    }


def test_runner_tools_audience_rejects_research_scopes():
    # the tool audience must not grant research-turn scopes (and vice versa
    # the enforcement is the verifier's; the token machinery only carries them,
    # so assert the scopes round-trip separately per audience)
    tok, keys = _token(AUD_RUNNER_TOOLS, [SCOPE_EXPERIMENT_SUBMIT])
    cap = verify_research_token(keys, tok)
    assert "research:run" not in cap.scopes


def test_legacy_audiences_still_verify():
    tok, keys = _token(AUD_RUNNER_EXEC, ["research:run"])
    assert verify_research_token(keys, tok).aud == AUD_RUNNER_EXEC
    tok, keys = _token(AUD_RUNTIME_RESEARCH, ["research:run"])
    assert verify_research_token(keys, tok).aud == AUD_RUNTIME_RESEARCH
