"""Opt-in live model acceptance; incurs provider requests, never runs by default.

ALPHA_MODEL_FILE=/private/model.json HERMES_IMAGE=sha256:... TEST_DATABASE_URL=...
uv run pytest -q --tb=short tests/hermes/provider_integration.py

Real provisioning, three role containers, native Runs and provider tool round trips.
The workspace tool response is a controlled nonce, not real market data. No passing
production registry is generated. Secrets are read from a private file, never args.
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from container_integration import api, docker, wait_ready
from docker_gateway import reserve_three_ports


def test_live_model_in_three_profiles_and_restart_replay(tmp_path):
    model_file = Path(os.environ["ALPHA_MODEL_FILE"]).resolve()
    image = os.environ["HERMES_IMAGE"]
    calls = []
    nonce = "workspace-" + uuid.uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"body": body, "authorization": self.headers.get("Authorization"),
                          "operation": self.headers.get("X-Alpha-Operation-Id")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"snapshot": {"artifacts": [], "acceptanceNonce": nonce},
                                        "version": 0}).encode())

    peer = ThreadingHTTPServer(
        (os.environ.get("ALPHA_TEST_BIND_HOST", "127.0.0.1"), 0), Handler
    )
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    port, sockets = reserve_three_ports()
    output = queue.Queue()
    log = (tmp_path / "prepare.log").open("w")
    node = subprocess.Popen(
        [shutil.which("node"), "--import", "tsx", "tests/support/hermes-container.ts"],
        env={key: os.environ[key] for key in ("PATH", "HOME", "TEST_DATABASE_URL")},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
    )

    def read_output():
        for line in node.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    prepared = []
    report = {"image": image, "roles": [], "marketData": "not_tested"}
    try:
        node.stdin.write(json.dumps({"image": image, "ports": [port],
            "outputRoot": str(tmp_path / "profiles"), "modelFile": str(model_file),
            "peerOrigin": f"http://host.docker.internal:{peer.server_port}"}) + "\n")
        node.stdin.flush()
        line = output.get(timeout=30)
        assert line, "Profile preparation failed; private log retained"
        prepared = json.loads(line)["prepared"]
        owner = prepared[0]
        compose = json.loads(Path(owner["composeFile"]).read_text())
        for sock in sockets:
            sock.close()
        sockets.clear()
        docker("compose", "-f", owner["composeFile"], "up", "-d", "--no-build", "--pull", "never")
        for config in compose["services"].values():
            role = config["labels"]["lab.alpha.role"]
            profile_id = config["labels"]["lab.alpha.profile"]
            directory = Path(owner["directory"]) / "profiles" / profile_id
            profile = {"container": config["container_name"],
                       "origin": "http://127.0.0.1:" + str(config["ports"][0]["published"]),
                       "apiKey": json.loads((directory / "credentials.json").read_text())["apiKey"]}
            wait_ready(profile)
            observed = json.loads(docker("inspect", profile["container"]))[0]
            assert observed["Image"] == image
            capability = uuid.uuid4().hex * 2
            headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Alpha-Tool-Capability": capability}
            body = {"input": "Call get_workspace exactly once. Read acceptanceNonce from the "
                             "returned snapshot and reply with that exact value only.",
                    "instructions": "This is a connectivity check. Use only get_workspace.",
                    "session_id": str(uuid.uuid4())}
            before = len(calls)
            accepted = api(profile, "/v1/runs", body, headers)
            deadline = time.monotonic() + 180
            run = {}
            while time.monotonic() < deadline:
                run = api(profile, "/v1/runs/" + accepted["run_id"])
                if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                    break
                time.sleep(0.5)
            assert run.get("status") == "completed", f"{role}: {run.get('status')}"
            assert nonce in run.get("output", ""), f"{role}: tool result missing from final output"
            assert len(calls) == before + 1, f"{role}: expected exactly one workspace call"
            call = calls[-1]
            assert call["body"] == {"tool": "get_workspace", "arguments": {}}
            assert call["authorization"] == "Bearer " + capability
            assert call["operation"]
            if role == "research":
                docker("stop", "--timeout", "30", profile["container"])
                docker("start", profile["container"])
                wait_ready(profile)
                replay = api(profile, "/v1/runs", body, headers)
                assert replay["run_id"] == accepted["run_id"]
                restored = api(profile, "/v1/runs/" + accepted["run_id"])
                assert restored["output"] == run["output"]
                assert len(calls) == before + 1
                report["restartReplay"] = True
            report["roles"].append({"role": role, "toolRoundTrip": True, "terminalOutput": True})
            print(role + ": live model tool round trip passed", flush=True)
        assert len(report["roles"]) == 3
        report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (tmp_path / "provider-evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        for sock in sockets:
            sock.close()
        for owner in prepared:
            docker("compose", "-f", owner["composeFile"], "down", "--timeout", "30")
        node.stdin.close()
        node.wait(timeout=30)
        assert node.returncode == 0, "Preparation helper failed; private log retained"
        log.close()
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)
