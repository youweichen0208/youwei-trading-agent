"""Explicit live model + live Alpaca research acceptance; incurs provider usage.

ALPHA_MODEL_FILE=/private/model.json ALPHA_MARKET_FILE=/private/alpaca.json
HERMES_IMAGE=sha256:... TEST_DATABASE_URL=... uv run pytest -q -s --tb=short
  tests/hermes/live_research_integration.py

Real tools, PostgreSQL, Quant, three isolated containers, Worker and Canvas facts.
Only the user identity and manually selected simulated holdings are test inputs.
This does not generate a passing production registry or deploy a public service.
"""

import json
import os
import queue
import shutil
import signal
import subprocess
import threading

from docker_gateway import DockerGateway, docker, reserve_three_ports


def test_live_three_role_research(tmp_path):
    image = os.environ["HERMES_IMAGE"]
    output = queue.Queue()
    port, reservations = reserve_three_ports()
    log = (tmp_path / "live-peer.log").open("w")
    node = subprocess.Popen(
        [shutil.which("node"), "--import", "tsx", "tests/support/hermes-live.ts"],
        env={
            key: os.environ[key]
            for key in ("PATH", "HOME", "TEST_DATABASE_URL", "ALPHA_TEST_BIND_HOST")
            if key in os.environ
        },
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

    def receive(timeout):
        line = output.get(timeout=timeout)
        assert line, "Live research peer failed; inspect private live-peer.log"
        return json.loads(line)

    try:
        node.stdin.write(
            json.dumps(
                {
                    "image": image,
                    "firstPort": port,
                    "outputRoot": str(tmp_path / "profiles"),
                    "evidenceRoot": str(tmp_path),
                    "modelFile": os.environ["ALPHA_MODEL_FILE"],
                    "marketFile": os.environ["ALPHA_MARKET_FILE"],
                }
            )
            + "\n"
        )
        node.stdin.flush()
        setup = receive(30)
        prepared = setup["prepared"]
        for sock in reservations:
            sock.close()
        reservations.clear()
        bindings = []
        for profile in setup["profiles"]:
            gateway = DockerGateway(prepared, profile, image)
            gateway.start()
            bindings.append(
                {
                    "profileId": profile["id"],
                    "origin": f"http://127.0.0.1:{gateway.port}",
                    "token": gateway.api_key,
                    "image": image,
                }
            )
        node.stdin.write(json.dumps(bindings) + "\n")
        node.stdin.flush()
        result = receive(660)
        assert result["passed"] is True
        assert result["nativeRuns"] == 3
        assert len(result["components"]) == 5
        assert result["syntheticMarket"] is False and result["syntheticModel"] is False
        print(json.dumps(result), flush=True)
    finally:
        for sock in reservations:
            sock.close()
        if prepared:
            docker("compose", "-f", prepared["composeFile"], "down", "--timeout", "30")
        node.stdin.close()
        try:
            node.wait(timeout=35)
        except subprocess.TimeoutExpired:
            os.killpg(node.pid, signal.SIGTERM)
            try:
                node.wait(timeout=35)
            except subprocess.TimeoutExpired:
                os.killpg(node.pid, signal.SIGKILL)
                node.wait(timeout=5)
        node.stdout.close()
        reader.join(timeout=5)
        log.close()
    assert node.returncode == 0
