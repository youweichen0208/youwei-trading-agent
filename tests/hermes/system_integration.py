"""Opt-in real Hermes -> HTTP tools -> PostgreSQL/Quant -> Worker integration.

Uses the same source variables as runtime_integration.py plus TEST_DATABASE_URL.
The model and market are synthetic; this never certifies a production deployment.
"""

import json
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import runtime_integration


@pytest.fixture(scope="module")
def installed_runtime():
    return runtime_integration.runtime_source.__wrapped__()


def input_context(messages):
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            continue
        start = content.find('{"')
        if start >= 0:
            value, _ = json.JSONDecoder().raw_decode(content[start:])
            if "input" in value:
                return value
    raise AssertionError("Task context absent from model input")


class ResearchModel:
    """Deterministic model decisions; all references come from actual tool replies."""

    def __init__(self):
        self.calls = []
        self.errors = []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply({"data": [{"id": "alpha-fixture", "context_length": 128000}]})

            def reply(self, value, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                role = self.path.split("/")[1]
                model.calls.append((role, body))
                try:
                    # Hermes may ask auxiliary title/summary questions without tools.
                    message = (
                        model.next(role, body)
                        if body.get("tools")
                        else {"role": "assistant", "content": "Synthetic integration research"}
                    )
                except Exception as exc:
                    model.errors.append(repr(exc))
                    self.reply({"error": {"message": "Test model assertion failed"}}, 500)
                    return
                finish = "tool_calls" if message.get("tool_calls") else "stop"
                envelope = {
                    "id": "chatcmpl-system-fixture",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "alpha-fixture",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240},
                }
                if not body.get("stream"):
                    self.reply(envelope)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                delta = dict(message)
                for index, tool in enumerate(delta.get("tool_calls", [])):
                    tool["index"] = index
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

    def next(self, role, body):
        messages = body["messages"]
        context = input_context(messages)
        last_user = max(i for i, message in enumerate(messages) if message.get("role") == "user")
        results = {}
        for message in messages[last_user + 1 :]:
            if message.get("role") == "tool":
                value = json.loads(message["content"])
                assert "error" not in value, value
                results[message["tool_call_id"]] = value

        def call(key, name, **arguments):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": key,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }

        def canvas(key, component, **refs):
            return call(key, "canvas_upsert", component=component, title=component, **refs)

        if context["input"] == "把它改成一年":
            assert role == "research"
            assert "NVDA" not in messages[last_user]["content"]
            previous = input_context(messages[:last_user])
            match = re.search(r"Analyze ([A-Z]+) from [0-9-]+ to ([0-9-]+)", previous["input"])
            assert match, "Previous confirmed question did not survive in native history"
            symbol, end = match.groups()
            assert symbol == "NVDA" and end == "2025-08-01"
            assert any(
                message.get("content") == "Three roles and five components completed."
                for message in messages[:last_user]
            ), "Previous final answer is missing"
            if "year_market" not in results:
                start = str(int(end[:4]) - 1) + end[4:]
                return call(
                    "year_market",
                    "market_history",
                    symbol=symbol,
                    start=start,
                    end=end,
                    adjustment="split",
                )
            dataset = results["year_market"]["result"]["datasetRef"]
            if "year_indicators" not in results:
                return call(
                    "year_indicators", "quant_calculate", kind="indicators", datasetRef=dataset
                )
            if "year_chart" not in results:
                return canvas("year_chart", "stock_chart", datasetRef=dataset)
            if "year_panel" not in results:
                return canvas(
                    "year_panel",
                    "indicator_panel",
                    resultRef=results["year_indicators"]["result"]["resultRef"],
                )
            return {"role": "assistant", "content": "NVDA one-year follow-up completed."}

        if role != "research":
            if role == "portfolio" and "holdings" not in results:
                return call(
                    "holdings",
                    "read_holdings",
                    portfolioSnapshotRef=context["portfolioSnapshotRef"],
                )
            if "calculation" not in results:
                args = (
                    {"datasetRef": context["datasetRefs"][0]}
                    if role == "prediction"
                    else {
                        "datasetRefs": context["datasetRefs"],
                        "portfolioSnapshotRef": context["portfolioSnapshotRef"],
                    }
                )
                return call("calculation", "quant_calculate", kind=role, **args)
            result = results["calculation"]["result"]["resultRef"]
            if "read" not in results:
                return call("read", "read_result", resultRef=result)
            if "publish" not in results:
                return canvas(
                    "publish",
                    "prediction_result" if role == "prediction" else "portfolio_summary",
                    resultRef=result,
                )
            return {"role": "assistant", "content": "Synthetic specialist result: " + result}

        for adjustment in ("split", "raw"):
            if adjustment not in results:
                return call(
                    adjustment,
                    "market_history",
                    symbol="NVDA",
                    start="2025-01-02",
                    end="2025-08-01",
                    adjustment=adjustment,
                )
        dataset = results["split"]["result"]["datasetRef"]
        if "indicators" not in results:
            return call("indicators", "quant_calculate", kind="indicators", datasetRef=dataset)
        indicators = results["indicators"]["result"]["resultRef"]
        # Return two tools in one native completion. This pinned Hermes planner
        # treats plugin calls as sequential barriers; no parallelism is claimed.
        if "chart" not in results and "panel" not in results:
            chart = canvas("chart", "stock_chart", datasetRef=dataset)
            panel = canvas("panel", "indicator_panel", resultRef=indicators)
            chart["tool_calls"].extend(panel["tool_calls"])
            return chart
        assert "chart" in results and "panel" in results
        for specialist in ("prediction", "portfolio"):
            if specialist not in results:
                args = (
                    {"portfolioSnapshotRef": context["portfolioSnapshotRef"]}
                    if specialist == "portfolio"
                    else {}
                )
                ref = (
                    dataset
                    if specialist == "prediction"
                    else results["raw"]["result"]["datasetRef"]
                )
                return call(
                    specialist,
                    "submit_specialist",
                    role=specialist,
                    requestKey=specialist,
                    question="Analyze the supplied references.",
                    datasetRefs=[ref],
                    **args,
                )
        references = [indicators]
        for specialist in ("prediction", "portfolio"):
            previous = [v for k, v in results.items() if k.startswith("wait_" + specialist)]
            if not previous or previous[-1]["status"] not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                return call(
                    f"wait_{specialist}_{len(previous)}",
                    "get_specialist_result",
                    childTaskId=results[specialist]["childTaskId"],
                )
            assert previous[-1]["status"] == "completed", previous[-1]
            references.extend(previous[-1]["output"]["resultRefs"])
        if "summary" not in results:
            return call(
                "summary",
                "save_research_summary",
                text="Synthetic integration evidence, not investment advice.",
                datasetRefs=[dataset],
                resultRefs=references,
            )
        if "summary_chart" not in results:
            return canvas(
                "summary_chart", "research_summary", resultRef=results["summary"]["resultRef"]
            )
        return {"role": "assistant", "content": "Three roles and five components completed."}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def test_three_real_profiles_with_worker_crash_and_cancellation(installed_runtime, tmp_path):
    run_three_profiles(tmp_path, installed_runtime=installed_runtime)


def run_three_profiles(tmp_path, *, installed_runtime=None, image=None):
    model = ResearchModel()
    gateways = []
    output = queue.Queue()
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TEST_DATABASE_URL")}
    if "ALPHA_TEST_BIND_HOST" in os.environ:
        env["ALPHA_TEST_BIND_HOST"] = os.environ["ALPHA_TEST_BIND_HOST"]
    log = (tmp_path / "system-peer.log").open("w+")
    node = subprocess.Popen(
        [shutil.which("node"), "--import", "tsx", "tests/support/hermes-system.ts"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        start_new_session=True,
    )

    def read_output():
        for line in node.stdout:
            output.put(line)
        output.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    prepared = None
    reservations = []

    def receive(timeout):
        line = output.get(timeout=timeout)
        if line is None:
            log.flush()
            raise AssertionError((tmp_path / "system-peer.log").read_text()[-6000:])
        return json.loads(line)

    try:
        setup = receive(30)
        if image:
            from docker_gateway import DockerGateway, docker, reserve_three_ports

            assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
            info = json.loads(docker("image", "inspect", image))[0]
            assert info["Config"]["Labels"]["lab.alpha.hermes.commit"] == (
                "3b421578588243f920886e6fe79a6faa87ea026e"
            )
            port, reservations = reserve_three_ports()
            node.stdin.write(
                json.dumps(
                    {
                        "action": "prepare-containers",
                        "image": image,
                        "firstPort": port,
                        "modelOrigin": model.origin,
                        "outputRoot": str(tmp_path / "profiles"),
                    }
                )
                + "\n"
            )
            node.stdin.flush()
            prepared = receive(30)["prepared"]
            for sock in reservations:
                sock.close()
            reservations.clear()
        bindings = []
        for profile in setup["profiles"]:
            if image:
                gateway = DockerGateway(prepared, profile, image)
            else:
                directory = tmp_path / profile["role"]
                directory.mkdir(mode=0o700)
                gateway = runtime_integration.GatewayProcess(
                    installed_runtime,
                    directory,
                    profile["role"],
                    SimpleNamespace(origin=model.origin + "/" + profile["role"]),
                    identity={
                        "version": 1,
                        "ownerId": setup["owner"],
                        "profileId": profile["id"],
                        "role": profile["role"],
                    },
                    tool_origin=setup["toolOrigin"],
                )
            gateways.append(gateway)
            gateway.start()
            bindings.append(
                {
                    "profileId": profile["id"],
                    "origin": f"http://127.0.0.1:{gateway.port}",
                    "token": gateway.api_key,
                    **({"image": image} if image else {}),
                }
            )
        node.stdin.write(json.dumps(bindings) + "\n")
        node.stdin.flush()
        restart_request = receive(250)
        assert restart_request == {"action": "restart-research"}, restart_request
        research = next(g for g in gateways if g.role == "research")
        previous_pid = research.process.pid
        research.stop()
        research.start()
        assert research.process.pid != previous_pid
        node.stdin.write(
            json.dumps(
                {"restarted": True, "previousPid": previous_pid, "pid": research.process.pid}
            )
            + "\n"
        )
        node.stdin.flush()
        result = receive(180)
        assert result["passed"] is True, result
        assert result["workerCrashRecovered"] is True
        assert result["cancellationWaitedForTool"] is True
        assert result["cancellationCases"] == 2
        assert result["researchCreates"] == 4
        assert result["totalCreates"] == 7
        assert result["followupPreservedSession"] is True
        assert result["followupAfterHermesRestart"] is True
        assert not model.errors, model.errors
        assert {role for role, body in model.calls if body.get("tools")} == {
            "research",
            "prediction",
            "portfolio",
        }
        assert all(g.api_key not in json.dumps(model.calls) for g in gateways)
        for gateway in gateways:
            with sqlite3.connect(gateway.home / ".alpha-private/bindings.db") as db:
                capabilities = db.execute("SELECT capability FROM bindings").fetchall()
            assert len(capabilities) == (4 if gateway.role == "research" else 1)
            assert all(row[0] not in json.dumps(model.calls) for row in capabilities)
        assert node.wait(timeout=10) == 0
    finally:
        # Stop runtimes before their tool peer, then wait for the peer's schema cleanup.
        for sock in reservations:
            sock.close()
        if prepared:
            subprocess.run(
                ["docker", "compose", "-f", prepared["composeFile"], "down", "--timeout", "30"],
                capture_output=True,
                timeout=90,
            )
        else:
            for gateway in gateways:
                gateway.stop()
        if node.poll() is None:
            # The peer owns Worker subprocesses. A test timeout must not leave
            # them running against a schema that the peer is cleaning up.
            os.killpg(node.pid, signal.SIGTERM)
            try:
                node.wait(timeout=35)
            except subprocess.TimeoutExpired:
                os.killpg(node.pid, signal.SIGKILL)
                node.wait(timeout=5)
        node.stdin.close()
        node.stdout.close()
        reader.join(timeout=5)
        log.close()
        model.close()


def test_application_recovery_with_real_gateway_and_postgres(installed_runtime, tmp_path):
    peers = runtime_integration.LocalPeers(hold_model=True)
    gateway = None
    messages = queue.Queue()
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TEST_DATABASE_URL")}
    log = (tmp_path / "recovery-peer.log").open("w+")
    node = subprocess.Popen(
        [shutil.which("node"), "--import", "tsx", "tests/support/hermes-recovery.ts"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        start_new_session=True,
    )

    def read_output():
        for line in node.stdout:
            messages.put(line)
        messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def receive():
        line = messages.get(timeout=45)
        if line is None:
            log.flush()
            raise AssertionError((tmp_path / "recovery-peer.log").read_text()[-6000:])
        return json.loads(line)

    def send(value):
        node.stdin.write(json.dumps(value) + "\n")
        node.stdin.flush()

    starts = stops = 0
    # Opaque test-process observation, never represented as a real Docker image.
    container_id = "b" * 64
    started_at = None
    try:
        setup = receive()
        gateway = runtime_integration.GatewayProcess(
            installed_runtime,
            tmp_path,
            "research",
            peers,
            identity={
                "version": 1,
                "ownerId": setup["owner"],
                "profileId": setup["profileId"],
                "role": "research",
            },
        )

        def start():
            nonlocal starts, started_at
            gateway.start()
            starts += 1
            started_at = f"test-process:{gateway.process.pid}:{time.monotonic_ns()}"

        start()
        send({"origin": f"http://127.0.0.1:{gateway.port}", "token": gateway.api_key})
        while True:
            message = receive()
            if message.get("passed"):
                assert message["taskStatus"] == "interrupted"
                assert message["auditSteps"] == 6
                break
            command = message["command"]
            value = None
            if command == "inspect":
                value = {
                    "id": container_id,
                    "startedAt": started_at,
                    "status": "running" if gateway.process.poll() is None else "stopped",
                }
            elif command == "healthy":
                gateway.api("/health")
                value = True
            elif command == "stop":
                gateway.stop()
                assert gateway.process.poll() is not None
                stops += 1
            elif command == "start":
                start()
            elif command == "crash-and-restart":
                assert peers.model_entered.wait(timeout=20), gateway.failure_log()
                os.killpg(gateway.process.pid, signal.SIGKILL)
                assert gateway.process.wait(timeout=5) == -signal.SIGKILL
                gateway.stop()
                peers.hold_model = False
                peers.model_release.set()
                start()
            else:
                raise AssertionError(command)
            send({"command": command, "value": value})
        assert starts == 3  # Initial, deliberate crash restart, controlled recovery restart.
        assert stops == 1
        assert not peers.tool_calls
        assert len([call for call in peers.model_calls if call.get("tools")]) == 1
        assert node.wait(timeout=10) == 0
    finally:
        peers.model_release.set()
        if gateway:
            gateway.stop()
        if node.poll() is None:
            os.killpg(node.pid, signal.SIGTERM)
            try:
                node.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(node.pid, signal.SIGKILL)
                node.wait(timeout=5)
        node.stdin.close()
        node.stdout.close()
        reader.join(timeout=5)
        log.close()
        peers.close()
