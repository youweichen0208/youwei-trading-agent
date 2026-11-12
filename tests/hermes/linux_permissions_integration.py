"""Opt-in real Linux UID/permission check using a daemon-local volume.

Docker Desktop bind mounts can translate ownership. A named Linux volume avoids
that translation; this tests the actual image startup against a mode-0600 home
marker prepared by a different host UID. It does not certify a Linux deployment.
HERMES_IMAGE=sha256:... uv run pytest -q tests/hermes/linux_permissions_integration.py
"""

import json
import os
import socket
import subprocess
import time
import uuid

import pytest
from container_integration import api, docker, terminal, wait_ready
from runtime_integration import LocalPeers


@pytest.mark.parametrize(
    "marker_kind", ["regular", "foreign", "symlink", "hardlink", "hardlink-runtime"]
)
def test_linux_private_home_identity_handoff(tmp_path, marker_kind):
    image = os.environ["HERMES_IMAGE"]
    suffix = uuid.uuid4().hex
    container, volume = "alpha-permissions-" + suffix, "alpha-home-" + suffix
    peers = LocalPeers()
    identity = {
        "version": 1,
        "ownerId": str(uuid.uuid4()),
        "profileId": str(uuid.uuid4()),
        "role": "research",
    }
    origin = f"http://host.docker.internal:{peers.server.server_port}"
    spec = identity | {
        "model": {
            "name": "alpha-fixture",
            "baseUrl": origin + "/v1",
            "apiMode": "chat_completions",
        },
        "gatewayOrigin": origin,
    }
    secret = {
        "apiKey": uuid.uuid4().hex * 2,
        "modelApiKey": "test-only-model-key-not-a-provider-secret",
    }
    for name, value in (("runtime.json", spec), ("credentials.json", secret)):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        path.chmod(0o444)
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    port = reserved.getsockname()[1]
    profile = {
        "container": container,
        "origin": f"http://127.0.0.1:{port}",
        "apiKey": secret["apiKey"],
    }
    mount = f"type=volume,source={volume},target=/opt/data,volume-nocopy"
    try:
        docker("volume", "create", volume)
        seed = """import json,os,pathlib,sys
home=pathlib.Path('/opt/data')
for name,content in [('.alpha-profile.json',sys.argv[1]),('unrelated.txt',sys.argv[1])]:
 p=home/name
 p.write_text(content)
 p.chmod(0o600)
 os.chown(p,17001,17001)
marker=home/'.alpha-profile.json'
kind=sys.argv[2]
if kind=='foreign':
 foreign=json.loads(sys.argv[1]);foreign['role']='portfolio'
 marker.write_text(json.dumps(foreign))
elif kind in ('symlink','hardlink','hardlink-runtime'):
 marker.unlink()
 if kind=='symlink':marker.symlink_to(home/'unrelated.txt')
 else:os.link(home/'unrelated.txt',marker)
 if kind=='hardlink-runtime':os.chown(marker,10000,10000)
home.chmod(0o700)
os.chown(home,17001,17001)
assert (home/'.alpha-profile.json').stat().st_uid==(10000 if kind=='hardlink-runtime' else 17001)
"""
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            mount,
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            image,
            "-c",
            seed,
            json.dumps(identity),
            marker_kind,
        )
        reserved.close()
        args = [
            "run",
            "-d",
            "--name",
            container,
            "--restart",
            "no",
            "--mount",
            mount,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "2g",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{port}:8642",
            "-e",
            "ALPHA_PROFILE_SPEC=/run/alpha/runtime.json",
            "-e",
            "ALPHA_PROFILE_SECRET=/run/secrets/profile_credentials",
        ]
        for cap in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"):
            args.extend(["--cap-add", cap])
        for name, target in (
            ("runtime.json", "/run/alpha/runtime.json"),
            ("credentials.json", "/run/secrets/profile_credentials"),
        ):
            args.extend(["--mount", f"type=bind,source={tmp_path / name},target={target},readonly"])
        docker(*args, image, "/opt/hermes/.venv/bin/python", "-m", "alpha_hermes.bootstrap")
        if marker_kind != "regular":
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = json.loads(docker("inspect", container))[0]["State"]
                if not state["Running"]:
                    break
                time.sleep(0.25)
            assert state["Status"] == "exited"
            logs = subprocess.run(
                ["docker", "logs", container], capture_output=True, text=True, timeout=10
            )
            assert "Alpha Profile initialization refused" in logs.stderr
            check = """import pathlib,sys
home=pathlib.Path('/opt/data')
uid=10000 if sys.argv[1]=='hardlink-runtime' else 17001
for name in ('.alpha-profile.json','unrelated.txt'):
 s=(home/name).stat()
 assert (s.st_uid,s.st_gid,s.st_mode&0o777)==(uid,uid,0o600)
assert not (home/'.alpha-private').exists()
"""
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                mount,
                "--entrypoint",
                "/opt/hermes/.venv/bin/python",
                image,
                "-c",
                check,
                marker_kind,
            )
            assert not peers.model_calls and not peers.tool_calls
            return
        try:
            wait_ready(profile)
        except AssertionError:
            logs = subprocess.run(
                ["docker", "logs", "--tail", "15", container],
                capture_output=True,
                text=True,
                timeout=10,
            )
            print(logs.stdout + logs.stderr)
            probe = """import os,pathlib
home=pathlib.Path('/opt/data')
for p in (home,home/'.alpha-profile.json'):
 s=p.stat();print(p.name,s.st_uid,s.st_gid,oct(s.st_mode&0o777))
os.setgroups([]);os.setgid(10000);os.setuid(10000)
print('home accessible:',os.access(home,os.R_OK|os.W_OK|os.X_OK))
print('marker readable:',os.access(home/'.alpha-profile.json',os.R_OK))
"""
            print(
                docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    mount,
                    "--entrypoint",
                    "/opt/hermes/.venv/bin/python",
                    image,
                    "-c",
                    probe,
                )
            )
            raise
        result = api(
            profile,
            "/v1/runs",
            {
                "input": "Use get_workspace once.",
                "session_id": str(uuid.uuid4()),
                "instructions": "Use project tools.",
            },
            {"Idempotency-Key": str(uuid.uuid4()), "X-Alpha-Tool-Capability": uuid.uuid4().hex * 2},
        )
        assert terminal(profile, result["run_id"])["status"] == "completed"
        assert len(peers.tool_calls) == 1
        check = """import pathlib
home=pathlib.Path('/opt/data')
marker=(home/'.alpha-profile.json').stat()
assert (marker.st_uid,marker.st_gid,marker.st_mode&0o777)==(10000,10000,0o600)
other=(home/'unrelated.txt').stat()
assert (other.st_uid,other.st_gid,other.st_mode&0o777)==(17001,17001,0o600)
assert home.stat().st_mode&0o777==0o700
assert (home/'.alpha-private/bindings.db').stat().st_mode&0o777==0o600
"""
        docker("exec", container, "/opt/hermes/.venv/bin/python", "-c", check)
    finally:
        reserved.close()
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=30)
        peers.close()
