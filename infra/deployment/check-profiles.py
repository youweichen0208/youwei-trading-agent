"""Certify the prepared deployment in place before starting the business tools.

Uses the configured real model and an explicitly synthetic, private get_workspace
endpoint. It does not claim to validate market data or business calculations.
Reports are written only after every actual Profile passes every assertion.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=100)
    if result.returncode:
        raise RuntimeError("Docker operation failed; inspect private deployment state")
    return result.stdout


def api(entry, path, body=None, extra=None, token=None):
    request = Request(
        entry["origin"] + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + (token or entry["token"]),
            "Content-Type": "application/json",
            **(extra or {}),
        },
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def until(action, seconds=120):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = action()
        if value:
            return value
        time.sleep(0.25)
    raise AssertionError("Profile contract check timed out")


def ready(entry):
    try:
        return api(entry, "/health")
    except (OSError, ValueError):
        return False


def terminal(entry, run):
    value = api(entry, "/v1/runs/" + run)
    return value if value["status"] in TERMINAL else False


def main():
    os.umask(0o077)
    directory = Path(sys.argv[1]).resolve()
    assert str(directory).startswith("/opt/hermes-alpha-lab/private/profiles/")
    entries = json.loads((directory / "profiles.unverified.json").read_text())
    assert len(entries) == 3
    model_timeout = int(os.environ.get("ALPHA_ACCEPTANCE_MODEL_TIMEOUT_SECONDS", "120"))
    assert 30 <= model_timeout <= 600
    fixtures = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            cap = self.headers.get("Authorization", "").removeprefix("Bearer ")
            fixture = fixtures.get(cap)
            if self.path != "/call" or fixture is None:
                self.send_error(401)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            valid = body == {"tool": "get_workspace", "arguments": {}}
            operation = self.headers.get("X-Alpha-Operation-Id", "")
            valid = valid and bool(re.fullmatch(r"hermes:[a-f0-9]{64}", operation))
            fixture["calls"].append({"valid": valid, "operation": operation})
            fixture["entered"].set()
            fixture["release"].wait(20)
            payload = json.dumps(
                {
                    "acceptanceFixture": True,
                    "nonce": fixture["nonce"],
                    "snapshot": {"artifacts": []},
                    "version": 0,
                }
            ).encode()
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            fixture["exited"].set()

    test_host = os.environ.get("ALPHA_ACCEPTANCE_BIND_HOST", "172.17.0.1")
    test_port = int(os.environ.get("ALPHA_ACCEPTANCE_TOOL_PORT", "3411"))
    assert 1024 <= test_port <= 65535 and test_port != 3410
    # Override only the test gateway mount; never edit sealed production specs.
    compose = json.loads((directory / "compose.json").read_text())
    test_compose = directory / "compose.acceptance.json"
    test_specs = []
    for entry in entries:
        spec_path = directory / "profiles" / entry["profileId"] / "runtime.json"
        spec = json.loads(spec_path.read_text())
        spec["gatewayOrigin"] = f"http://host.docker.internal:{test_port}"
        temporary = spec_path.with_name("runtime.acceptance.json")
        temporary.write_text(json.dumps(spec))
        temporary.chmod(0o444)
        test_specs.append(temporary)
        for mount in compose["services"]["profile-" + entry["profileId"]]["volumes"]:
            if mount["target"] == "/run/alpha/runtime.json":
                mount["source"] = str(temporary)
    test_compose.write_text(json.dumps(compose))
    test_compose.chmod(0o600)
    server = ThreadingHTTPServer((test_host, test_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    observations = []
    try:
        for entry in entries:
            profile = directory / "profiles" / entry["profileId"]
            entry["token"] = json.loads((profile / "credentials.json").read_text())["apiKey"]
        assert len({e["token"] for e in entries}) == 3
        networks, homes = set(), set()
        docker(
            "compose",
            "-f",
            str(test_compose),
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
        )
        for index, entry in enumerate(entries):
            until(lambda entry=entry: ready(entry))
            name = "alpha-" + entry["profileId"]
            actual = json.loads(docker("inspect", name))[0]
            assert actual["Image"] == entry["imageDigest"]
            host = actual["HostConfig"]
            assert not host["Privileged"] and host["PidMode"] != "host"
            assert host["RestartPolicy"]["Name"] == "no"
            assert host["CapDrop"] == ["ALL"]
            assert "no-new-privileges:true" in host["SecurityOpt"]
            assert host["Memory"] == 2 * 1024**3 and host["PidsLimit"] == 256
            assert host["PortBindings"]["8642/tcp"] == [
                {"HostIp": "127.0.0.1", "HostPort": entry["origin"].rsplit(":", 1)[1]}
            ]
            assert actual["Config"]["Labels"]["lab.alpha.profile"] == entry["profileId"]
            mounted = {m["Destination"]: m for m in actual["Mounts"]}
            assert set(mounted) == {
                "/opt/data",
                "/run/alpha/runtime.json",
                "/run/secrets/profile_credentials",
            }
            assert all(m["RW"] == (dest == "/opt/data") for dest, m in mounted.items())
            homes.add(mounted["/opt/data"]["Source"])
            nets = actual["NetworkSettings"]["Networks"]
            assert len(nets) == 1
            networks.update(nets)
            try:
                api(entry, "/v1/runs/unknown", token=entries[(index + 1) % 3]["token"])
                raise AssertionError("Foreign Profile credential accepted")
            except HTTPError as error:
                assert error.code == 401
                error.close()

            completed = None
            for stopping in (False, True):
                cap, session, key = secrets.token_hex(32), str(uuid.uuid4()), str(uuid.uuid4())
                fixture = {
                    "nonce": secrets.token_hex(12),
                    "calls": [],
                    "entered": threading.Event(),
                    "release": threading.Event(),
                    "exited": threading.Event(),
                }
                fixtures[cap] = fixture
                if not stopping:
                    fixture["release"].set()
                request = {
                    "session_id": session,
                    "input": (
                        "Invoke the get_workspace function through the tool-calling interface exactly once. Merely writing its name is not a tool call. Reply only with the nonce field "
                        "from its response. This is a deployment connection check; "
                        "do not call other tools."
                    ),
                    "instructions": (
                        "You must perform a real get_workspace tool invocation before responding. Do not output the tool name as text. Read and quote the nonce returned by the tool. Do not invent a nonce."
                    ),
                }
                headers = {"Idempotency-Key": key, "X-Alpha-Tool-Capability": cap}
                accepted = api(entry, "/v1/runs", request, headers)["run_id"]
                if not fixture["entered"].wait(model_timeout):
                    # Keep private diagnostics before an operator restores the old home.
                    diagnostic = directory / "contract-failure.json"
                    diagnostic.write_text(json.dumps({
                        "profileId": entry["profileId"],
                        "stoppingCheck": stopping,
                        "run": api(entry, "/v1/runs/" + accepted),
                    }) + "\n")
                    diagnostic.chmod(0o600)
                    raise AssertionError("Real model did not call the required tool; private diagnostics saved")
                if stopping:
                    stopped = api(entry, "/v1/runs/" + accepted + "/stop", {})
                    assert stopped["status"] == "stopping"
                    for _ in range(4):
                        assert not terminal(entry, accepted), (
                            "Terminal status preceded active tool exit"
                        )
                        time.sleep(0.25)
                    fixture["release"].set()
                result = until(lambda entry=entry, accepted=accepted: terminal(entry, accepted))
                assert result["session_id"] == session
                assert fixture["exited"].is_set()
                assert len(fixture["calls"]) == 1 and fixture["calls"][0]["valid"]
                if stopping:
                    assert result["status"] in {"cancelled", "interrupted"}
                else:
                    assert result["status"] == "completed" and fixture["nonce"] in result["output"]
                    assert api(entry, "/v1/runs", request, headers)["run_id"] == accepted
                    completed = (request, headers, accepted, result["output"], fixture)
                observations.append(
                    {
                        "profileId": entry["profileId"],
                        "runId": accepted,
                        "sessionId": session,
                        "status": result["status"],
                        "stoppedDuringTool": stopping,
                    }
                )

            request, headers, accepted, output, fixture = completed
            before = len(fixture["calls"])
            docker("stop", "--time", "45", name)
            docker("start", name)
            until(lambda entry=entry: ready(entry))
            assert json.loads(docker("inspect", name))[0]["Id"] == actual["Id"]
            assert api(entry, "/v1/runs", request, headers)["run_id"] == accepted
            replay = api(entry, "/v1/runs/" + accepted)
            assert replay["status"] == "completed" and replay["output"] == output
            assert replay["session_id"] == request["session_id"]
            assert len(fixture["calls"]) == before
            print(json.dumps({"profileId": entry["profileId"], "contract": "passed"}), flush=True)
        assert len(networks) == len(homes) == 3
        # Remove test containers only after Docker confirms their stop; production supervisor
        # will create fresh containers from the original, unchanged compose specification.
        docker("compose", "-f", str(test_compose), "down", "--timeout", "45")
        evidence = directory / "evidence"
        evidence.mkdir(mode=0o700, exist_ok=True)
        for entry in entries:
            report = {k: entry[k] for k in ("profileId", "origin", "imageDigest")}
            report.update(
                checkedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                retentionMs=60000,
                checks={
                    key: True
                    for key in [
                        "profileIsolation",
                        "durableIdempotency",
                        "restartReplay",
                        "sessionIdentity",
                        "stopWaitsForExit",
                        "terminalOutput",
                        "toolIdentityInjection",
                    ]
                },
            )
            (evidence / (entry["profileId"] + ".json")).write_text(json.dumps(report) + "\n")
        (evidence / "observations.json").write_text(
            json.dumps(
                {"realModel": True, "fixtureToolResponse": True, "observations": observations},
                indent=2,
            )
            + "\n"
        )
        # Secrets are omitted from the registry. All checks above must succeed.
        (directory / "profiles.verified.json").write_text(
            json.dumps([{k: v for k, v in e.items() if k != "token"} for e in entries], indent=2)
            + "\n"
        )
    finally:
        for fixture in fixtures.values():
            fixture["release"].set()
        # Failure paths also stop all three test Profiles. Failure here leaves the
        # caller's durable capacity reservation unknown until explicit reconciliation.
        docker("compose", "-f", str(test_compose), "down", "--timeout", "45")
        for temporary in test_specs:
            temporary.unlink(missing_ok=True)
        test_compose.unlink(missing_ok=True)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
