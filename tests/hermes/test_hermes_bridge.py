import contextvars
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType, SimpleNamespace

import pytest
from alpha_hermes import bridge

RUN_A = "run_" + "a" * 32
RUN_B = "run_" + "b" * 32
CAP_A = "1" * 64
CAP_B = "2" * 64


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_BINDING_DB", str(tmp_path / "private" / "bindings.db"))
    monkeypatch.setenv("ALPHA_ROLE", "research")
    canonical = ModuleType("gateway.session_api_turn")
    canonical.api_execution = contextvars.ContextVar("test_api_execution", default=None)
    monkeypatch.setitem(sys.modules, "gateway.session_api_turn", canonical)
    executor = ModuleType("agent.tool_executor")
    executor._ManagedToolResult = SimpleNamespace
    monkeypatch.setitem(sys.modules, "agent.tool_executor", executor)
    return canonical.api_execution


@pytest.fixture
def gateway(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((dict(self.headers), body))
            mode = body["arguments"].get("mode")
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/leaked")
                self.end_headers()
                return
            data = json.dumps({"result": self.headers["Authorization"] if mode == "echo"
                               else body["arguments"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ALPHA_TOOL_ORIGIN", f"http://127.0.0.1:{server.server_port}")
    try:
        yield calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_bindings_are_durable_immutable_and_profile_private(runtime, tmp_path):
    bridge.bind_admission(RUN_A, "session-a", CAP_A)
    bridge.bind_admission(RUN_A, "session-a", CAP_A)
    reopened = bridge.BindingStore(bridge.store().path)
    assert reopened.get(RUN_A).capability == CAP_A
    assert CAP_A not in repr(reopened.get(RUN_A))
    assert os.stat(reopened.path).st_mode & 0o777 == 0o600
    assert os.stat(reopened.path.parent).st_mode & 0o777 == 0o700
    with pytest.raises(bridge.BridgeError, match="binding_conflict"):
        reopened.bind(RUN_A, "session-a", CAP_B)
    with pytest.raises(bridge.BridgeError, match="binding_conflict"):
        reopened.bind(RUN_A, "session-b", CAP_A)
    other_profile = bridge.BindingStore(tmp_path / "other" / "bindings.db")
    with pytest.raises(bridge.BridgeError, match="missing_run_binding"):
        other_profile.get(RUN_A)
    assert reopened.get(RUN_A).capability == CAP_A


def test_request_contract_prevents_body_identity_and_capability_replay(runtime):
    body = {"input": "question", "session_id": "session-a", "instructions": "research"}
    headers = {"Idempotency-Key": "key-a", "X-Alpha-Tool-Capability": CAP_A}
    assert bridge.validate_request(body, headers, True) == CAP_A
    assert bridge.fingerprint("native-fingerprint", CAP_A) != bridge.fingerprint(
        "native-fingerprint", CAP_B)
    for extra in [{"alpha_run_id": RUN_A}, {"ownerId": "other"}, {"model": "other"},
                  {"conversation_history": []}, {"_room_execution_policy": {}}]:
        with pytest.raises(bridge.BridgeError, match="invalid_alpha_run_request"):
            bridge.validate_request(body | extra, headers, True)
    with pytest.raises(bridge.BridgeError, match="canonical_authority_required"):
        bridge.validate_request(body, headers, False)
    with pytest.raises(bridge.BridgeError, match="missing_execution_capability"):
        bridge.validate_request(body, {"Idempotency-Key": "key-a"}, True)


def test_concurrent_calls_keep_original_run_and_stable_operation_ids(runtime, gateway):
    bridge.bind_admission(RUN_A, "shared-session", CAP_A)
    bridge.bind_admission(RUN_B, "shared-session", CAP_B)
    rendezvous = threading.Barrier(2)

    def call(run_id):
        token = runtime.set({"alpha_run_id": run_id})
        try:
            with bridge.tool_scope("provider-call-1"):
                rendezvous.wait(timeout=3)
                # Neither capability nor operation key is added to model args.
                result = bridge.invoke("get_workspace", {"label": run_id})
            after = json.loads(bridge.invoke("get_workspace", {}))
            assert after["error"] == "missing_tool_identity"
            return json.loads(result)
        finally:
            runtime.reset(token)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(call, [RUN_A, RUN_B]))
    assert {r["result"]["label"] for r in results} == {RUN_A, RUN_B}
    for headers, body in gateway:
        run = body["arguments"]["label"]
        assert headers["Authorization"] == "Bearer " + (CAP_A if run == RUN_A else CAP_B)
        assert headers["X-Alpha-Operation-Id"] == bridge.operation_id(run, "provider-call-1")
        assert set(body) == {"tool", "arguments"}
        assert CAP_A not in json.dumps(body) and CAP_B not in json.dumps(body)
    token = runtime.set({"alpha_run_id": RUN_A})
    try:
        with bridge.tool_scope("provider-call-1"):
            bridge.invoke("get_workspace", {})
    finally:
        runtime.reset(token)
    assert gateway[-1][0]["Authorization"] == "Bearer " + CAP_A
    assert gateway[-1][0]["X-Alpha-Operation-Id"] == bridge.operation_id(RUN_A, "provider-call-1")


def test_missing_context_roles_and_builtin_tools_fail_closed(runtime, gateway, monkeypatch):
    bridge.bind_admission(RUN_A, "session", CAP_A)
    assert json.loads(bridge.invoke("get_workspace", {"alpha_run_id": RUN_A,
                                                      "capability": CAP_A}))["error"]
    called = []

    @bridge.guard_dispatch
    def dispatch(**kwargs):
        called.append(kwargs)
        return bridge.invoke(kwargs["function_name"], kwargs["function_args"])

    args = {"function_name": "get_workspace", "function_args": {}, "tool_call_id": "call-1"}
    advanced = []
    assert dispatch(**args, begin_execution=lambda: advanced.append(True)).blocked
    assert advanced == [True]
    token = runtime.set({"alpha_run_id": RUN_A})
    try:
        assert dispatch(**(args | {"function_name": "terminal"})).blocked
        monkeypatch.setenv("ALPHA_ROLE", "prediction")
        assert dispatch(**(args | {"function_name": "submit_specialist"})).blocked
        assert json.loads(dispatch(**args)) == {"result": {}}
    finally:
        runtime.reset(token)
    assert len(called) == len(gateway) == 1


def test_redirect_and_secret_echo_do_not_reach_model(runtime, gateway):
    bridge.bind_admission(RUN_A, "session", CAP_A)
    token = runtime.set({"alpha_run_id": RUN_A})
    try:
        for mode in ["redirect", "echo"]:
            with bridge.tool_scope("call-" + mode):
                response = bridge.invoke("get_workspace", {"mode": mode})
            assert "error" in json.loads(response)
            assert CAP_A not in response
    finally:
        runtime.reset(token)
    assert len(gateway) == 2  # the 307 target was never requested


def test_plugin_registers_role_catalog_without_credential(runtime):
    registered = []
    bridge.register(SimpleNamespace(register_tool=lambda **kwargs: registered.append(kwargs)))
    assert "submit_specialist" in {tool["name"] for tool in registered}
    for tool in registered:
        assert tool["toolset"] == "alpha_lab"
        assert "capability" not in json.dumps(tool["schema"])
        assert json.loads(tool["handler"]({}))["error"] == "missing_tool_identity"
