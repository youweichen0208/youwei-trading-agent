"""Opt-in full Hermes process test with controlled local model and tool servers.

HERMES_SOURCE=/original/pinned/source HERMES_RUNTIME_SOURCE=/patched/installed/source \
  uv run pytest -q tests/hermes/runtime_integration.py

Requires the pinned Python 3.14 runtime with frozen core + messaging dependencies.
This is not provider, container-isolation or production registration evidence.
"""

import hashlib
import json
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from alpha_hermes import bridge
from alpha_hermes.bootstrap import gateway_environment, prepare
from apply_patch import patched_sources


@pytest.fixture(scope="module")
def runtime_source():
    original = Path(os.environ["HERMES_SOURCE"]).resolve()
    installed = Path(os.environ["HERMES_RUNTIME_SOURCE"]).resolve()
    pin = json.loads(Path("services/hermes/upstream.json").read_text())
    for name, digest in pin["runtimeFiles"].items():
        data = (installed / name).read_text().rstrip("\n") + "\n"
        assert hashlib.sha256(data.encode()).hexdigest() == digest, name
    for name, digest in pin["auditFiles"].items():
        for root in (original, installed):
            data = (root / name).read_text().rstrip("\n") + "\n"
            assert hashlib.sha256(data.encode()).hexdigest() == digest, name
    for path, expected in patched_sources(original).items():
        assert (installed / path.relative_to(original)).read_text() == expected, path
    for package, destination in [("alpha_hermes", "alpha_hermes"), ("plugin", "plugins/alpha-lab")]:
        for file in (Path("services/hermes") / package).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts:
                assert (
                    installed / destination / file.relative_to(Path("services/hermes") / package)
                ).read_bytes() == file.read_bytes(), file
    assert (installed / ".venv/bin/hermes").is_file()
    version = subprocess.check_output(
        [str(installed / ".venv/bin/python"), "-c", "import sys;print(sys.version_info[:2])"],
        text=True,
        timeout=5,
    ).strip()
    assert version == "(3, 14)", version
    return installed


class LocalPeers:
    def __init__(self, *, hold_model=False):
        self.model_calls = []
        self.tool_calls = []
        self.hold_model = hold_model
        self.model_entered = threading.Event()
        self.model_release = threading.Event()
        peers = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply(
                    {
                        "object": "list",
                        "data": [
                            {"id": "alpha-fixture", "object": "model", "context_length": 128000}
                        ],
                    }
                )

            def reply(self, data):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/call":
                    peers.tool_calls.append({"headers": dict(self.headers), "body": body})
                    self.reply({"fixture": True, "snapshot": {"artifacts": []}, "version": 0})
                    return
                peers.model_calls.append(body)
                if peers.hold_model and body.get("tools"):
                    peers.model_entered.set()
                    peers.model_release.wait(timeout=60)
                    # This request belongs to the gateway that the test killed.
                    return
                final = not body.get("tools") or body["messages"][-1].get("role") == "tool"
                message = (
                    {"role": "assistant", "content": "Alpha bridge process test completed."}
                    if final
                    else {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "alpha_call_1",
                                "type": "function",
                                "function": {"name": "get_workspace", "arguments": "{}"},
                            }
                        ],
                    }
                )
                finish = "stop" if final else "tool_calls"
                envelope = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "alpha-fixture",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                }
                if not body.get("stream"):
                    self.reply(envelope)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                delta = dict(message)
                if "tool_calls" in delta:
                    delta["tool_calls"][0]["index"] = 0
                for part, end in [(delta, None), ({}, finish)]:
                    payload = {
                        **envelope,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": part, "finish_reason": end}],
                    }
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(
            (os.environ.get("ALPHA_TEST_BIND_HOST", "127.0.0.1"), 0), Handler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://{self.server.server_address[0]}:{self.server.server_port}"

    def close(self):
        self.model_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class GatewayProcess:
    def __init__(self, source, directory, role, peers, *, identity=None, tool_origin=None):
        self.role = role
        self.source = source
        self.directory = directory
        self.home = directory / "home"
        self.home.mkdir(mode=0o700)
        self.api_key = uuid.uuid4().hex + uuid.uuid4().hex
        self.capability = uuid.uuid4().hex + uuid.uuid4().hex
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        identity = identity or {
            "version": 1,
            "ownerId": str(uuid.uuid4()),
            "profileId": str(uuid.uuid4()),
            "role": role,
        }
        (self.home / ".alpha-profile.json").write_text(json.dumps(identity))
        spec = identity | {
            "model": {
                "name": "alpha-fixture",
                "baseUrl": peers.origin + "/v1",
                "apiMode": "chat_completions",
            },
            "gatewayOrigin": "http://host.docker.internal:3410",
        }
        settings = prepare(
            self.home,
            spec,
            {"apiKey": self.api_key, "modelApiKey": "test-only-model-key-not-a-provider-secret"},
        )
        settings["ALPHA_TOOL_ORIGIN"] = tool_origin or peers.origin
        config = json.loads((self.home / "config.yaml").read_text())
        config["platforms"]["api_server"].update(host="127.0.0.1", port=self.port)
        config["model"]["context_length"] = 128000
        (self.home / "config.yaml").write_text(json.dumps(config))
        # No inherited provider credentials, other Hermes homes or real databases.
        self.env = gateway_environment(
            {
                "PATH": os.environ["PATH"],
                "HOME": str(directory),
                "HERMES_HOME": str(self.home),
                "HERMES_RUNTIME_DIR": str(directory / "runtime"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            settings,
        )
        self.process = None
        self.log = None

    def api(self, path, body=None, headers=None):
        request = Request(
            f"http://127.0.0.1:{self.port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
            | (headers or {}),
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def start(self):
        self.log = (self.directory / "gateway.log").open("a")
        self.process = subprocess.Popen(
            [str(self.source / ".venv/bin/hermes"), "gateway", "run"],
            cwd=self.source,
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        until = time.monotonic() + 60
        while time.monotonic() < until:
            assert self.process.poll() is None, self.failure_log()
            try:
                self.api("/health")
                return
            except (OSError, ValueError):
                time.sleep(0.25)
        raise AssertionError("gateway health timeout: " + self.failure_log())

    def failure_log(self):
        return (self.directory / "gateway.log").read_text()[-4000:]

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def terminal(self, run_id):
        until = time.monotonic() + 45
        while time.monotonic() < until:
            status = self.api("/v1/runs/" + run_id)
            if status["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return status
            time.sleep(0.25)
        raise AssertionError("run did not terminate: " + self.failure_log())


@pytest.mark.parametrize("role", ["research", "prediction", "portfolio"])
def test_full_process_role_tools_and_research_restart(runtime_source, tmp_path, role):
    peers = LocalPeers()
    gateway = GatewayProcess(runtime_source, tmp_path, role, peers)
    try:
        gateway.start()
        session = str(uuid.uuid4())
        key = str(uuid.uuid4())
        request = {
            "input": "Use get_workspace once and report the result.",
            "session_id": session,
            "instructions": "Use only project tools.",
        }
        headers = {"Idempotency-Key": key, "X-Alpha-Tool-Capability": gateway.capability}
        accepted = gateway.api("/v1/runs", request, headers)
        result = gateway.terminal(accepted["run_id"])
        assert result["status"] == "completed", result
        assert result["session_id"] == session
        assert result["output"] == "Alpha bridge process test completed."
        # A model's final sentence alone is not evidence that any tool ran.
        assert len(peers.tool_calls) == 1, peers.model_calls
        observed = peers.tool_calls[0]
        assert observed["headers"]["Authorization"] == "Bearer " + gateway.capability
        assert observed["headers"]["X-Alpha-Operation-Id"] == bridge.operation_id(
            accepted["run_id"], "alpha_call_1"
        )
        assert observed["body"] == {"tool": "get_workspace", "arguments": {}}
        expected = {tool["name"] for tool in bridge.catalog()[role]}
        calls = [call for call in peers.model_calls if call.get("tools")]
        assert calls and all(
            {tool["function"]["name"] for tool in call["tools"]} == expected for call in calls
        )
        assert gateway.capability not in json.dumps(peers.model_calls)
        assert gateway.api_key not in json.dumps(peers.model_calls)
        replay = gateway.api("/v1/runs", request, headers)
        assert replay["run_id"] == accepted["run_id"]
        assert len(peers.tool_calls) == 1
        with pytest.raises(HTTPError) as conflict:
            gateway.api("/v1/runs", request, headers | {"X-Alpha-Tool-Capability": "c" * 64})
        assert conflict.value.code == 409
        conflict.value.close()
        if role == "research":
            gateway.stop()
            gateway.start()
            replay = gateway.api("/v1/runs", request, headers)
            assert replay["run_id"] == accepted["run_id"]
            restored = gateway.terminal(accepted["run_id"])
            assert restored["output"] == result["output"]
            assert len(peers.tool_calls) == 1
    finally:
        gateway.stop()
        peers.close()


def test_running_gateway_crash_pauses_fifo_until_exact_unknown_resolution(runtime_source, tmp_path):
    peers = LocalPeers(hold_model=True)
    gateway = GatewayProcess(runtime_source, tmp_path, "research", peers)
    try:
        gateway.start()
        session = str(uuid.uuid4())
        request = {
            "input": "Use get_workspace once and report the result.",
            "session_id": session,
            "instructions": "Use only project tools.",
        }
        headers = {
            "Idempotency-Key": str(uuid.uuid4()),
            "X-Alpha-Tool-Capability": gateway.capability,
        }
        accepted = gateway.api("/v1/runs", request, headers)
        run_id = accepted["run_id"]
        assert peers.model_entered.wait(timeout=20), gateway.failure_log()
        before = gateway.api("/v1/runs/" + run_id)
        assert before["status"] == "running", before
        pid = gateway.process.pid
        os.killpg(pid, signal.SIGKILL)
        assert gateway.process.wait(timeout=5) == -signal.SIGKILL
        gateway.stop()  # Close the old log without touching another process.
        peers.hold_model = False
        peers.model_release.set()
        gateway.start()
        assert gateway.process.pid != pid
        restored = gateway.api("/v1/runs/" + run_id)
        assert restored["status"] == "unknown", restored
        assert restored["admission_id"] == before["admission_id"]
        assert restored["execution_generation"] == before["execution_generation"]
        assert restored["session_id"] == session
        assert gateway.api("/v1/runs", request, headers)["run_id"] == run_id
        assert len([call for call in peers.model_calls if call.get("tools")]) == 1
        assert not peers.tool_calls

        following = gateway.api(
            "/v1/runs", request, headers | {"Idempotency-Key": str(uuid.uuid4())}
        )
        assert following["run_id"] != run_id
        assert gateway.api("/v1/runs/" + following["run_id"])["status"] == "queued"
        endpoint = "/v1/runs/" + run_id + "/resolve-unknown"
        evidence = {
            "admission_id": restored["admission_id"],
            "execution_generation": restored["execution_generation"],
        }
        for wrong, status in [
            (evidence | {"execution_generation": evidence["execution_generation"] + 1}, 409),
            (evidence | {"admission_id": str(uuid.uuid4())}, 409),
        ]:
            with pytest.raises(HTTPError) as refused:
                gateway.api(endpoint, wrong)
            assert refused.value.code == status
            refused.value.close()
        assert gateway.api("/v1/runs/" + run_id)["status"] == "unknown"
        assert not peers.tool_calls

        # Explicit test operator action after confirmed SIGKILL exit; not an
        # automatic production recovery rule or proof that remote tools exited.
        resolution = gateway.api(endpoint, evidence)
        assert gateway.api(endpoint, evidence) == resolution
        ended = gateway.terminal(run_id)
        assert ended["status"] == "cancelled", ended  # Native projection of interrupted.
        assert not ended["output"]
        assert gateway.terminal(following["run_id"])["status"] == "completed"
        assert len(peers.tool_calls) == 1  # Only the follower, never the lost turn.
        assert peers.tool_calls[0]["headers"]["X-Alpha-Operation-Id"] == bridge.operation_id(
            following["run_id"], "alpha_call_1"
        )
        assert gateway.api("/v1/runs", request, headers)["run_id"] == run_id
        assert len(peers.tool_calls) == 1
    finally:
        peers.model_release.set()
        gateway.stop()
        peers.close()
