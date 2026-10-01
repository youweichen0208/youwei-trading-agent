"""S07m: platform research-tool surface (snapshot_manifest) — pure, no Hermes.

These tests pin the tool-authorization model and the snapshot_manifest
handler's output without importing the pinned Hermes checkout: the handler
must only read the run's frozen evidence (from the contextvar), must re-verify
the Controller's Ed25519 research grant and required scope before acting, and
must report the evidence manifest exactly as frozen (no live data, no DB). The
research container holds only PUBLIC keys (it verifies, never signs).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    sign_research_token,
)

from youwei_agent_runtime.tools import (
    RESEARCH_TOOL_DEFINITIONS,
    RESEARCH_TOOLSET,
    TOOL_REQUIRED_SCOPE,
    ToolAuthorizationError,
    ToolContext,
    current_tool_context,
    require_scope,
    reset_tool_context,
    set_tool_context,
    snapshot_manifest_handler,
    snapshot_manifest_schema,
)


@pytest.fixture(scope="module")
def keypair():
    priv, pub = generate_research_keypair()
    return priv, pub


def _evidence(**overrides) -> FrozenEvidence:
    sec = str(uuid.uuid4())
    bars = [
        {"security_id": sec, "trade_date": "2026-09-25", "close": 100.0},
        {"security_id": sec, "trade_date": "2026-09-24", "close": 99.0},
    ]
    import hashlib

    content_sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    manifest = {
        "code_version": "daily-bars-snapshot-v1",
        "schema": "daily_bars_v1",
        "row_count": 3,
        "coverage": {"2026-09-25": 1},
        "sources": ["tiingo-daily-v1"],
    }
    base = dict(
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
            "manifest": manifest,
        },
        target_policy_sha256="d" * 64,
        batch_manifest={},
    )
    base.update(overrides)
    return FrozenEvidence(**base)


def _token(priv, evidence, scopes=(SCOPE_RESEARCH_RUN,), *, exp=None):
    return sign_research_token(
        priv,
        kid="k1",
        aud=AUD_RUNTIME_RESEARCH,
        invocation_id=uuid.uuid4(),
        tenant_id=evidence.tenant_id,
        run_id=evidence.run_id,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=evidence.case.case_id,
        evidence_sha256="e" * 64,
        exec_config_version="v1",
        scopes=scopes,
        exp=exp or (datetime.now(UTC) + timedelta(minutes=5)),
    )


@pytest.fixture
def ctx(keypair):
    priv, pub = keypair
    evidence = _evidence()
    token = _token(priv, evidence)
    tc = ToolContext(evidence=evidence, capability_token=token, public_keys={"k1": pub})
    tok = set_tool_context(tc)
    yield tc
    reset_tool_context(tok)


# --- context management ----------------------------------------------------


def test_current_tool_context_raises_without_run():
    with pytest.raises(ToolAuthorizationError, match="outside a run"):
        current_tool_context()


def test_set_and_reset_tool_context(ctx):
    assert current_tool_context() is ctx
    reset_tool_context(set_tool_context(ctx))


# --- require_scope ---------------------------------------------------------


def test_require_scope_accepts_valid_scope(ctx):
    require_scope(ctx, TOOL_REQUIRED_SCOPE)  # does not raise


def test_require_scope_rejects_missing_scope(ctx, keypair):
    priv, pub = keypair
    evidence = _evidence()
    token = _token(priv, evidence, scopes=("snapshot_read",))
    tc = ToolContext(evidence=evidence, capability_token=token, public_keys={"k1": pub})
    with pytest.raises(ToolAuthorizationError, match="missing required scope"):
        require_scope(tc, TOOL_REQUIRED_SCOPE)


def test_require_scope_rejects_bad_signature(ctx, keypair):
    priv, pub = keypair
    evidence = _evidence()
    tc = ToolContext(evidence=evidence, capability_token="ywr_garbage", public_keys={"k1": pub})
    with pytest.raises(ToolAuthorizationError, match="capability rejected"):
        require_scope(tc, TOOL_REQUIRED_SCOPE)


def test_require_scope_rejects_expired(ctx, keypair):
    priv, pub = keypair
    evidence = _evidence()
    token = _token(priv, evidence, exp=datetime.now(UTC) - timedelta(minutes=1))
    tc = ToolContext(evidence=evidence, capability_token=token, public_keys={"k1": pub})
    with pytest.raises(ToolAuthorizationError, match="capability rejected"):
        require_scope(tc, TOOL_REQUIRED_SCOPE)


# --- snapshot_manifest handler --------------------------------------------


def test_snapshot_manifest_handler_reports_frozen_manifest(ctx):
    out = json.loads(snapshot_manifest_handler({}))
    ev = ctx.evidence.evidence
    assert out["snapshot_id"] == str(ev.snapshot_id)
    assert out["kind"] == ev.kind
    assert out["as_of"] == ev.as_of
    assert out["mode"] == ev.mode
    assert out["content_sha256"] == ev.content_sha256
    assert out["row_count"] == ev.manifest["row_count"]
    assert out["schema"] == ev.manifest["schema"]
    assert out["coverage"] == ev.manifest["coverage"]
    assert out["sources"] == ev.manifest["sources"]
    assert out["code_version"] == ev.manifest["code_version"]


def test_snapshot_manifest_handler_uses_content_length_when_row_count_absent(ctx, keypair):
    priv, pub = keypair
    evidence = _evidence()
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 1.0}]
    import hashlib

    content_sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    ev2 = FrozenEvidence(
        run_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        case=evidence.case,
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00+00:00",
            "mode": "forward",
            "content_sha256": content_sha,
            "content": bars,
            "manifest": {"code_version": "v1"},
        },
        target_policy_sha256="d" * 64,
        batch_manifest={},
    )
    token = _token(priv, ev2)
    tc = ToolContext(evidence=ev2, capability_token=token, public_keys={"k1": pub})
    tok = set_tool_context(tc)
    try:
        out = json.loads(snapshot_manifest_handler({}))
        assert out["row_count"] == len(bars)  # fallback to len(content)
    finally:
        reset_tool_context(tok)


def test_snapshot_manifest_handler_rejects_tampered_content(ctx):
    ctx.evidence.evidence.content[0]["close"] = 12345.0
    with pytest.raises(ValueError, match="hash mismatch"):
        snapshot_manifest_handler({})


def test_snapshot_manifest_handler_requires_scope(keypair):
    priv, pub = keypair
    evidence = _evidence()
    token = _token(priv, evidence, scopes=("snapshot_read",))  # wrong scope
    tc = ToolContext(evidence=evidence, capability_token=token, public_keys={"k1": pub})
    tok = set_tool_context(tc)
    try:
        with pytest.raises(ToolAuthorizationError, match="scope"):
            snapshot_manifest_handler({})
    finally:
        reset_tool_context(tok)


# --- schema and registry shape --------------------------------------------


def test_snapshot_manifest_schema_shape():
    schema = snapshot_manifest_schema()
    assert schema["name"] == "snapshot_manifest"
    assert schema["parameters"] == {
        "type": "object", "properties": {}, "additionalProperties": False
    }
    assert "description" in schema


def test_research_tool_definitions_registry():
    names = [name for name, _schema, _handler in RESEARCH_TOOL_DEFINITIONS]
    assert names == ["snapshot_manifest"]
    assert RESEARCH_TOOLSET == "youwei-research"
    assert TOOL_REQUIRED_SCOPE == "research:run"
