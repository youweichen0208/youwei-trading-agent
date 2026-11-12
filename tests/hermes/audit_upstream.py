"""Explicit source-contract audit, NOT a real provider/runtime integration report.

HERMES_SOURCE=/verified/checkout uv run --with ruamel.yaml --with pyyaml \
  --with aiohttp --with python-dotenv pytest -q tests/hermes/audit_upstream.py
Runs the pinned upstream function bodies with controlled boundaries, including
its real durable idempotency store. Missing/mismatched source fails, never skips.
"""

import ast
import asyncio
import contextvars
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alpha_hermes import bridge
from apply_patch import patched_sources


def definitions(source, names, namespace):
    tree = ast.parse(source)
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    assert {node.name for node in nodes} == set(names)
    prefix = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(body=prefix + nodes, type_ignores=[]), "pinned_hermes", "exec"),
         namespace)


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    root = Path(os.environ["HERMES_SOURCE"]).resolve()
    sources = {
        str(path.relative_to(root)): source for path, source in patched_sources(root).items()
    }
    pin = json.loads(Path("services/hermes/upstream.json").read_text())
    for name, digest in pin["auditFiles"].items():
        text = (root / name).read_text().rstrip("\n") + "\n"
        assert hashlib.sha256(text.encode()).hexdigest() == digest, name
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setenv("ALPHA_BINDING_DB", str(tmp_path / "private" / "bindings.db"))
    monkeypatch.setenv("ALPHA_ROLE", "research")
    return root, sources


def test_native_runs_replay_after_reopen_rejects_changed_capability(
    upstream, tmp_path, monkeypatch,
):
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    _, sources = upstream
    module = ModuleType("alpha_audit_runs")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    admitted = []

    def response(body, status=200, headers=None):
        return SimpleNamespace(body=body, status=status, headers=headers or {})

    async def admit(owner, launch, *args):
        # At the precise moment the upstream FIFO would become runnable the
        # private binding must already be durable, even on another connection.
        binding = bridge.store().get(launch.run_id)
        assert binding.session_id == launch.session_id
        assert binding.capability == "1" * 64
        assert binding.capability not in json.dumps(launch.agent_kwargs)
        admitted.append(launch)
        launch.admission = True

    module.__dict__.update({
        "dataclass": dataclass, "asyncio": asyncio, "time": time, "uuid": uuid,
        "hashlib": hashlib, "json": json, "suppress": suppress,
        "web": SimpleNamespace(json_response=response), "_RunStream": asyncio.Queue,
        "_json_error": lambda fn, message, status=400, **kw: response({"error": message}, status),
        "_resolve_chained_history": AsyncMock(return_value=([], "research", None, None, None)),
        "_resolve_live_session_id": AsyncMock(side_effect=lambda owner, sid: sid),
        "_room_retention_until": lambda request: 0,
        "_retarget_to_delivered_chat": lambda *args: None,
        "_admit_run_to_authority": admit,
        "_execute_run": AsyncMock(),
    })
    definitions(sources["gateway/platforms/api_server_runs.py"], [
        "_handle_runs", "_RunLaunch", "_run_user_message", "_run_idempotency_identity",
        "_accepted_response", "_replay_or_conflict",
    ], module.__dict__)
    api = SimpleNamespace(_openai_error=None,
                          _request_turn_author=lambda body: None,
                          _request_agent_overrides=lambda body, **kw: {},
                          _api_request_profile=contextvars.ContextVar("profile", default="alpha"))
    ledger_path = str(tmp_path / "native-idempotency.db")

    def owner():
        ledger = RunIdempotencyStore(ledger_path)
        return SimpleNamespace(
            gateway_runner=SimpleNamespace(session_authority=object()),
            _parse_session_key_header=lambda req: (None, None),
            _normalize_room_dispatch=AsyncMock(side_effect=lambda req, body: (body, None)),
            _run_idempotency_scope=lambda req: "private-principal",
            _resolve_route=lambda model: None, _model_name="controlled-model",
            _request_route_conflict_error=lambda **kw: None,
            _run_idempotency_store=ledger, _concurrency_limited_response=lambda: None,
            _run_owners={}, _run_streams={}, _run_streams_created={}, _run_approval_sessions={},
            _conversation_history_for_session=AsyncMock(return_value=[]),
            _set_run_status=lambda rid, status, **kw: dict(status=status, **kw),
            _run_owner_pid=os.getpid(), _run_owner_started=1, _run_idempotency_ids=set(),
            _activate_admitted_request=lambda: None, _active_run_tasks={}, _background_tasks=set(),
            _durable_run_status=lambda req, rid: None,
        )

    async def exercise():
        first_owner = owner()
        req = SimpleNamespace(headers={"Idempotency-Key": "stable-key",
                                       "X-Alpha-Tool-Capability": "1" * 64},
                              json=AsyncMock(return_value={"input": "question",
                                  "session_id": str(uuid.uuid4()), "instructions": "research"}))
        first = await module._handle_runs(first_owner, req, _api_server=api)
        assert first.status == 202 and len(admitted) == 1
        await asyncio.gather(*first_owner._active_run_tasks.values())
        first_owner._run_idempotency_store._conn.close()
        restarted = owner()
        try:
            replay = await module._handle_runs(restarted, req, _api_server=api)
            assert replay.body["run_id"] == first.body["run_id"]
            assert replay.headers["Idempotency-Replayed"] == "true"
            assert len(admitted) == 1
            req.headers["X-Alpha-Tool-Capability"] = "2" * 64
            rejected = await module._handle_runs(restarted, req, _api_server=api)
            assert rejected.status == 409 and len(admitted) == 1
            del req.headers["X-Alpha-Tool-Capability"]
            rejected = await module._handle_runs(restarted, req, _api_server=api)
            assert rejected.status == 400
            req.headers["X-Alpha-Tool-Capability"] = "1" * 64
            restarted._run_idempotency_store._db_path = None
            rejected = await module._handle_runs(restarted, req, _api_server=api)
            assert rejected.status == 400
            assert rejected.body["error"] == "durable_idempotency_required"
        finally:
            restarted._run_idempotency_store._conn.close()

    asyncio.run(exercise())


def test_native_admission_persists_only_opaque_run_id(upstream, monkeypatch):
    _, sources = upstream
    authority = SimpleNamespace(logical_owner=lambda sid: sid,
                                _require_admission_open=lambda: None,
                                db=SimpleNamespace(get_session=lambda sid: None))
    authorities = ModuleType("gateway.session_authorities")
    authorities.require_active_authority = lambda *args: authority
    monkeypatch.setitem(sys.modules, "gateway.session_authorities", authorities)

    def payload(*args):
        yield args[4]

    namespace = {"uuid": uuid, "json": json, "_json": json.dumps, "_SETTING_KEYS": (),
                 "_admit_api_payload": payload, "_valid_owner_scope": lambda scope: True,
                 "check_api_turn": lambda *args: object(), "api_settings": lambda *args: {}}
    definitions(sources["gateway/session_api_turn.py"],
                ["admit_api_steps", "prepare_api_execution"], namespace)
    rid = "run_" + "a" * 32
    bridge.bind_admission(rid, "session", "1" * 64)
    kwargs = {"session_id": "session", "user_message": "question", "conversation_history": []}
    adapter = SimpleNamespace(gateway_runner=object(), _ensure_session_db=lambda: None)
    saved = next(namespace["admit_api_steps"](adapter, **kwargs, active_run_id=rid))
    assert saved["api_turn_v1"]["alpha_run_id"] == rid
    assert "1" * 64 not in json.dumps(saved)
    restored = namespace["prepare_api_execution"](authority, None, saved)
    assert restored["alpha_run_id"] == rid
    unbound = next(namespace["admit_api_steps"](adapter, **kwargs))
    assert "alpha_run_id" not in unbound["api_turn_v1"]
    assert namespace["prepare_api_execution"](authority, None, unbound)["alpha_run_id"] is None


def test_native_parser_denies_builtins_before_inline_dispatch(upstream):
    _, sources = upstream
    namespace = {"_canonical_tool_name": lambda name: name,
                 "_ParsedCall": lambda *args: args,
                 "_parse_tool_arguments": lambda args: ({}, None),
                 "_unwrap_tool_search_call": lambda agent, name, args, **kw: (name, args, None)}
    definitions(sources["agent/tool_executor.py"], ["_parse_tool_call"], namespace)
    for name in ["terminal", "read_file", "execute_code", "delegate_task", "tool_search"]:
        call = SimpleNamespace(function=SimpleNamespace(name=name, arguments="{}"))
        parsed = namespace["_parse_tool_call"](None, call)
        assert parsed[4] == "Alpha tool not allowed"
    call = SimpleNamespace(function=SimpleNamespace(name="get_workspace", arguments="{}"))
    assert namespace["_parse_tool_call"](None, call)[4] is None


def test_native_anthropic_builder_uses_only_explicit_scoped_auth_header(upstream, monkeypatch):
    _, sources = upstream
    omitted = object()
    sdk = SimpleNamespace(Omit=lambda: omitted, Anthropic=lambda **kwargs: kwargs)
    namespace = {
        "_require_sdk": lambda *args: sdk,
        "normalize_proxy_env_vars": lambda: None,
        "_base_client_kwargs": lambda url, timeout: (url, {"base_url": url}),
        "_common_betas_for_base_url": lambda *args, **kwargs: [],
        "_auth_style": lambda *args: "api_key",
        "_beta_header": lambda *args: {},
        "_is_opencode_endpoint": lambda *args: False,
        "_custom_provider_extra_headers": lambda *args: {},
    }
    definitions(sources["agent/anthropic_adapter.py"],
                ["build_anthropic_client", "_new_sdk_client"], namespace)
    endpoint = "https://model.invalid/compatible/anthropic"
    monkeypatch.setenv("ALPHA_MODEL_BASE_URL", endpoint)
    monkeypatch.setenv("ALPHA_MODEL_API_KEY", "test-provider-key")
    monkeypatch.setenv("ALPHA_MODEL_AUTH_MODE", "bearer")
    build = namespace["build_anthropic_client"]
    matched = build("test-provider-key", endpoint)
    assert matched["auth_token"] == "test-provider-key"
    assert "api_key" not in matched
    assert matched["default_headers"]["X-Api-Key"] is omitted
    for key, url in [("test-provider-key", "https://other.invalid"),
                     ("different-provider-key", endpoint)]:
        other = build(key, url)
        assert other["api_key"] == key and "auth_token" not in other
        assert other["default_headers"]["Authorization"] is omitted


def test_anthropic_tool_schemas_preserve_role_arguments(upstream):
    root, _ = upstream
    namespace = {"_EMPTY_SCHEMA": {"type": "object", "properties": {}}}
    definitions((root / "agent/anthropic_message_convert.py").read_text(),
                ["_normalize_tool_input_schema"], namespace)
    normalize = namespace["_normalize_tool_input_schema"]
    catalog = json.loads(Path("services/hermes/alpha_hermes/tools.json").read_text())
    for role, tools in catalog.items():
        for tool in tools:
            schema = normalize(tool["inputSchema"])
            assert not ({"anyOf", "oneOf", "allOf"} & schema.keys())
            if tool["name"] == "quant_calculate":
                expected = ({"kind", "portfolioSnapshotRef", "datasetRefs"}
                            if role == "portfolio" else {"kind", "datasetRef"})
                assert expected <= schema.get("properties", {}).keys(), (role, tool["name"])
                assert expected <= set(schema.get("required", [])), (role, tool["name"])
            if tool["name"] == "canvas_upsert":
                assert {"component", "title", "artifactId", "resultRef"} <= (
                    schema.get("properties", {}).keys()), (role, tool["name"])
                if role == "research":
                    assert "datasetRef" in schema["properties"]
                assert {"component", "title"} <= set(schema.get("required", []))


def test_patcher_refuses_modified_source_before_writing(upstream, tmp_path):
    root, _ = upstream
    pin = json.loads(Path("services/hermes/upstream.json").read_text())
    for file in pin["files"]:
        target = tmp_path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((root / file).read_text())
    changed = tmp_path / "agent/tool_executor.py"
    changed.write_text(changed.read_text() + "# modified\n")
    with pytest.raises(ValueError, match="upstream_source_mismatch"):
        patched_sources(tmp_path)


@pytest.mark.parametrize("group", ["auditFiles", "runtimeFiles"])
def test_image_patcher_checks_unmodified_dependencies_before_any_patch(upstream, tmp_path, group):
    """Image-build entrypoint must reject every pinned, non-patched dependency."""
    import subprocess

    root, _ = upstream
    pin = json.loads(Path("services/hermes/upstream.json").read_text())
    pristine = {}
    for collection in ("files", "auditFiles", "runtimeFiles"):
        for name in pin[collection]:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            pristine[name] = (root / name).read_text()
            target.write_text(pristine[name])
    for name in pin[group]:
        changed = tmp_path / name
        changed.write_text(pristine[name] + "\n# changed dependency\n")
        result = subprocess.run(
            [sys.executable, "services/hermes/apply_patch.py", str(tmp_path)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode != 0, name
        assert "upstream_source_mismatch: " + name in result.stderr
        # A late mismatch cannot leave a partly patched installation behind.
        for edited in pin["files"]:
            assert (tmp_path / edited).read_text() == pristine[edited], edited
        changed.write_text(pristine[name])
    result = subprocess.run(
        [sys.executable, "services/hermes/apply_patch.py", str(tmp_path)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    expected = patched_sources(root)
    for target, source in expected.items():
        assert (tmp_path / target.relative_to(root)).read_text() == source
