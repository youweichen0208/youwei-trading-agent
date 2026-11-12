"""Opt-in Docker container isolation/restart test with synthetic peers.

HERMES_IMAGE=sha256:<built image ID> TEST_DATABASE_URL=... uv run pytest -q \
  tests/hermes/container_integration.py

Uses real preparation, Compose, upstream s6, bootstrap and native Runs. No
passing production registry is generated. Model/tool responses are fixtures;
provider authorization and the real-data deployment gate are separate.
"""

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_integration import LocalPeers


def docker(*args, timeout=90):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout


def api(profile, path, body=None, headers=None, key=None):
    request = Request(
        profile["origin"] + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + (key or profile["apiKey"]),
            "Content-Type": "application/json",
        }
        | (headers or {}),
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def wait_ready(profile):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = json.loads(docker("inspect", profile["container"]))[0]["State"]
        assert state["Running"], docker("logs", "--tail", "30", profile["container"])
        try:
            api(profile, "/health")
            return
        except (OSError, ValueError):
            time.sleep(0.25)
    raise AssertionError("Profile container did not become healthy")


def terminal(profile, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        run = api(profile, "/v1/runs/" + run_id)
        if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return run
        time.sleep(0.25)
    raise AssertionError("Container run did not terminate")


def crash_gateway(profile):
    docker(
        "exec",
        profile["container"],
        "/opt/hermes/.venv/bin/python",
        "-c",
        "import pathlib,os,signal\n"
        "for p in pathlib.Path('/proc').iterdir():\n"
        " if p.name.isdigit():\n"
        "  try:\n"
        "   if b'gateway\\x00run' in (p/'cmdline').read_bytes(): "
        "os.kill(int(p.name),signal.SIGKILL)\n"
        "  except OSError: pass\n",
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        crashed = json.loads(docker("inspect", profile["container"]))[0]
        if not crashed["State"]["Running"]:
            break
        time.sleep(0.25)
    assert crashed["State"]["Status"] == "exited"
    assert crashed["RestartCount"] == 0


def test_two_users_three_profiles_real_containers_and_restart(tmp_path):
    image = os.environ["HERMES_IMAGE"]
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    info = json.loads(docker("image", "inspect", image))[0]
    assert info["Config"]["Labels"]["lab.alpha.hermes.commit"] == (
        "3b421578588243f920886e6fe79a6faa87ea026e"
    )
    assert info["Config"]["Entrypoint"] == ["/opt/hermes/docker/entrypoint-dispatch.sh"]
    # Bind three adjacent ephemeral ports per owner until configuration exists.
    reservations = []
    first_ports = []
    for _ in range(2):
        for _attempt in range(100):
            group = []
            try:
                first = socket.socket()
                group.append(first)
                first.bind(("127.0.0.1", 0))
                port = first.getsockname()[1]
                if port > 65533:
                    raise OSError("port range overflow")
                for offset in (1, 2):
                    sock = socket.socket()
                    group.append(sock)
                    sock.bind(("127.0.0.1", port + offset))
                first_ports.append(port)
                reservations.extend(group)
                break
            except OSError:
                for sock in group:
                    sock.close()
        else:
            raise AssertionError("No free adjacent port triple")
    peers = LocalPeers()
    output = queue.Queue()
    log = (tmp_path / "prepare-peer.log").open("w+")
    node = subprocess.Popen(
        [shutil.which("node"), "--import", "tsx", "tests/support/hermes-container.ts"],
        env={key: os.environ[key] for key in ("PATH", "HOME", "TEST_DATABASE_URL")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
    )

    def read_output():
        for line in node.stdout:
            output.put(line)
        output.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    prepared = []
    profiles = []
    try:
        # Docker Desktop uses the default loopback peer. Linux runs explicitly
        # bind ALPHA_TEST_BIND_HOST to its private Docker gateway interface.
        node.stdin.write(
            json.dumps(
                {
                    "image": image,
                    "ports": first_ports,
                    "outputRoot": str(tmp_path / "profiles"),
                    "peerOrigin": f"http://host.docker.internal:{peers.server.server_port}",
                }
            )
            + "\n"
        )
        node.stdin.flush()
        line = output.get(timeout=30)
        assert line is not None, (tmp_path / "prepare-peer.log").read_text()[-3000:]
        prepared = json.loads(line)["prepared"]
        for owner in prepared:
            directory = Path(owner["directory"])
            compose = json.loads(Path(owner["composeFile"]).read_text())
            for service, config in compose["services"].items():
                identity = config["labels"]
                profile_id = identity["lab.alpha.profile"]
                secret = json.loads(
                    (directory / "profiles" / profile_id / "credentials.json").read_text()
                )
                home = directory / "profiles" / profile_id / "home"
                memory = home / "memories"
                memory.mkdir(mode=0o700)
                memory_file = memory / "MEMORY.md"
                memory_file.write_text("private-memory-" + profile_id)
                memory_file.chmod(0o600)
                profiles.append(
                    {
                        "ownerId": owner["ownerId"],
                        "id": profile_id,
                        "role": identity["lab.alpha.role"],
                        "container": config["container_name"],
                        "service": service,
                        "compose": owner["composeFile"],
                        "home": str(directory / "profiles" / profile_id / "home"),
                        "origin": "http://127.0.0.1:" + config["ports"][0]["published"],
                        "apiKey": secret["apiKey"],
                        "capability": uuid.uuid4().hex * 2,
                    }
                )
        assert len(profiles) == 6 and len({p["apiKey"] for p in profiles}) == 6
        for sock in reservations:
            sock.close()
        reservations.clear()
        for owner in prepared:
            docker(
                "compose", "-f", owner["composeFile"], "up", "-d", "--no-build", "--pull", "never"
            )
        for index, profile in enumerate(profiles):
            wait_ready(profile)
            observed = json.loads(docker("inspect", profile["container"]))[0]
            assert observed["Image"] == image
            assert observed["Config"]["Labels"]["lab.alpha.owner"] == profile["ownerId"]
            assert observed["Config"]["Labels"]["lab.alpha.profile"] == profile["id"]
            host = observed["HostConfig"]
            assert not host["Privileged"] and host["RestartPolicy"]["Name"] == "no"
            assert host["CapDrop"] == ["ALL"]
            assert {cap.removeprefix("CAP_") for cap in host["CapAdd"]} == {
                "CHOWN",
                "DAC_OVERRIDE",
                "FOWNER",
                "SETGID",
                "SETUID",
                "KILL",
            }
            assert host["Memory"] == 2 * 1024**3 and host["NanoCpus"] == 10**9
            assert host["PidsLimit"] == 256 and "no-new-privileges:true" in host["SecurityOpt"]
            assert host["PidMode"] != "host" and host["NetworkMode"] != "host"
            assert len(observed["NetworkSettings"]["Networks"]) == 1
            mounts = observed["Mounts"]
            assert {m["Destination"] for m in mounts} == {
                "/opt/data",
                "/run/alpha/runtime.json",
                "/run/secrets/profile_credentials",
            }
            assert all(m["RW"] == (m["Destination"] == "/opt/data") for m in mounts)
            assert (
                next(m["Source"] for m in mounts if m["Destination"] == "/opt/data")
                == profile["home"]
            )
            assert host["PortBindings"]["8642/tcp"] == [
                {
                    "HostIp": "127.0.0.1",
                    "HostPort": profile["origin"].rsplit(":", 1)[1],
                }
            ]
            # Inspect actual gateway processes, not just docker exec's selected UID.
            process_check = """import pathlib,json
found=[]
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit(): continue
 try:
  cmd=(proc/'cmdline').read_bytes()
  uid=next(x for x in (proc/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1:]
  if b'gateway\\x00run' in cmd: found.append(uid)
 except (OSError,StopIteration): pass
print(json.dumps(found))"""
            uids = json.loads(
                docker(
                    "exec",
                    profile["container"],
                    "/opt/hermes/.venv/bin/python",
                    "-c",
                    process_check,
                )
            )
            assert uids == [["10000"] * 4], uids
            assert (
                docker(
                    "exec",
                    profile["container"],
                    "find",
                    "/run/hermes-service",
                    "-name",
                    "gateway-*",
                    "-type",
                    "d",
                ).strip()
                == ""
            ), "Native s6 gateway slots must not bypass host lifecycle control"
            foreign = profiles[(index + 3) % 6]
            try:
                api(profile, "/v1/runs/unknown", key=foreign["apiKey"])
                raise AssertionError("Foreign Profile API key was accepted")
            except HTTPError as refused:
                assert refused.code == 401
                refused.close()
            session = str(uuid.uuid4())
            request = {
                "input": "Use get_workspace once and report the result.",
                "session_id": session,
                "instructions": "Use only project tools.",
            }
            headers = {
                "Idempotency-Key": str(uuid.uuid4()),
                "X-Alpha-Tool-Capability": profile["capability"],
            }
            before_models = len(peers.model_calls)
            accepted = api(profile, "/v1/runs", request, headers)
            result = terminal(profile, accepted["run_id"])
            assert result["status"] == "completed" and result["session_id"] == session, result
            assert result["output"] == "Alpha bridge process test completed."
            model_calls = [c for c in peers.model_calls[before_models:] if c.get("tools")]
            assert model_calls and all(
                "private-memory-" + profile["id"] in json.dumps(c) for c in model_calls
            )
            for other in profiles:
                if other["id"] != profile["id"]:
                    assert "private-memory-" + other["id"] not in json.dumps(model_calls)
            calls = [
                c
                for c in peers.tool_calls
                if c["headers"]["Authorization"] == "Bearer " + profile["capability"]
            ]
            assert len(calls) == 1 and calls[0]["body"]["tool"] == "get_workspace"
            assert api(profile, "/v1/runs", request, headers)["run_id"] == accepted["run_id"]
            profile.update(
                request=request, headers=headers, run=accepted["run_id"], original=observed
            )
        research = next(p for p in profiles if p["role"] == "research")
        docker("stop", "--timeout", "30", research["container"])
        assert not json.loads(docker("inspect", research["container"]))[0]["State"]["Running"]
        docker("start", research["container"])
        wait_ready(research)
        restarted = json.loads(docker("inspect", research["container"]))[0]
        assert restarted["Id"] == research["original"]["Id"]
        assert restarted["State"]["StartedAt"] != research["original"]["State"]["StartedAt"]
        assert (
            api(research, "/v1/runs", research["request"], research["headers"])["run_id"]
            == research["run"]
        )
        assert terminal(research, research["run"])["status"] == "completed"
        assert len(peers.tool_calls) == 6, "Restart/replay duplicated a tool call"
        # Killing the actual gateway must stop the container, not let s6 launch
        # a replacement execution behind the host supervisor's recovery gate.
        crash_gateway(research)
        # This boot sees persisted 'running' native state left by SIGKILL.
        docker("start", research["container"])
        wait_ready(research)
        assert (
            docker(
                "exec",
                research["container"],
                "find",
                "/run/hermes-service",
                "-name",
                "gateway-*",
                "-type",
                "d",
            ).strip()
            == ""
        )
        assert terminal(research, research["run"])["status"] == "completed"
        assert len(peers.tool_calls) == 6
        node.stdin.write(json.dumps({"action": "lifecycle", "profileId": research["id"]}) + "\n")
        node.stdin.flush()
        line = output.get(timeout=90)
        assert line is not None, (tmp_path / "prepare-peer.log").read_text()[-3000:]
        lifecycle = json.loads(line)
        assert lifecycle == {
            "lifecycle": "passed",
            "containerId": research["original"]["Id"],
        }
        assert terminal(research, research["run"])["status"] == "completed"
        assert len(peers.tool_calls) == 6
        # Crash a genuinely running, Worker-admitted model turn, before any
        # external tool starts. The Node peer then uses the real recovery driver.
        before_crash_models = len(peers.model_calls)
        peers.hold_model = True
        node.stdin.write(
            json.dumps({"action": "crash-recovery", "profileId": research["id"]}) + "\n"
        )
        node.stdin.flush()
        line = output.get(timeout=30)
        assert line is not None, (tmp_path / "prepare-peer.log").read_text()[-3000:]
        assert json.loads(line) == {"crash": "admitted"}
        assert peers.model_entered.wait(timeout=30), "Native model execution never started"
        crash_gateway(research)
        peers.hold_model = False
        peers.model_release.set()
        node.stdin.write(json.dumps({"crashed": True}) + "\n")
        node.stdin.flush()
        line = output.get(timeout=120)
        assert line is not None, (tmp_path / "prepare-peer.log").read_text()[-3000:]
        assert json.loads(line) == {
            "crashRecovery": "passed",
            "taskStatus": "interrupted",
            "auditSteps": 6,
        }
        assert len([c for c in peers.model_calls[before_crash_models:] if c.get("tools")]) == 1
        assert len(peers.tool_calls) == 6, "Unknown recovery repeated external work"
        assert all(
            p["apiKey"] not in json.dumps(peers.model_calls)
            and p["capability"] not in json.dumps(peers.model_calls)
            for p in profiles
        )
    finally:
        for sock in reservations:
            sock.close()
        # Only the two random test Compose projects can be removed here.
        for owner in prepared:
            subprocess.run(
                ["docker", "compose", "-f", owner["composeFile"], "down", "--timeout", "10"],
                capture_output=True,
                timeout=60,
            )
        if node.poll() is None:
            node.stdin.close()
            try:
                node.wait(timeout=10)
            except subprocess.TimeoutExpired:
                node.terminate()
                node.wait(timeout=10)
        peers.close()
        log.close()
