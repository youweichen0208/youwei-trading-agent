"""Validate pinned monitoring rules and actual HTTP probes without touching app services."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROMETHEUS = (
    "prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
)
BLACKBOX = (
    "prom/blackbox-exporter@sha256:ea1c7aa3ef3f307cd03e85f34abb604478ccb90b567fc690da0cd9776da76b13"
)
FIXTURE = """from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(503 if self.path == '/fail' else 302 if self.path == '/redirect' else 200)
  self.send_header('Content-Type','application/json')
  self.send_header('Location','/ready')
  self.end_headers()
  self.wfile.write(b'{"status":"not_ready"}' if self.path == '/bad' else b'{"status":"ready"}')
 def log_message(self,*args): pass
HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
"""


def docker(*args):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=45
    ).stdout.strip()


def main():
    directory = str(ROOT / "infra/monitoring")
    for command in (
        ["test", "rules", "alerts.test.yaml"],
        ["check", "config", "/etc/prometheus/prometheus.yaml"],
    ):
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "-v",
            directory + ":/etc/prometheus:ro",
            "--workdir",
            "/etc/prometheus",
            "--entrypoint",
            "/bin/promtool",
            PROMETHEUS,
            *command,
        )
    prefix = "yta-monitor-check-" + uuid4().hex[:10]
    network, fixture, probe = prefix, prefix + "-fixture", prefix + "-blackbox"
    try:
        docker("network", "create", network)
        docker(
            "run",
            "-d",
            "--name",
            fixture,
            "--network",
            network,
            "--network-alias",
            "fixture",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "python",
            "yta-alpha:local",
            "-c",
            FIXTURE,
        )
        docker(
            "run",
            "-d",
            "--name",
            probe,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-p",
            "127.0.0.1::9115",
            "-v",
            directory + ":/config:ro",
            BLACKBOX,
            "--config.file=/config/blackbox.yaml",
        )
        ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", probe))
        port = ports["9115/tcp"][0]["HostPort"]
        origin = f"http://127.0.0.1:{port}"
        checks = {}
        with httpx.Client(trust_env=False, timeout=8) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if client.get(origin + "/metrics").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Blackbox did not start")
                time.sleep(0.1)
            for path, expected in [("ready", 1), ("bad", 0), ("fail", 0), ("redirect", 0)]:
                response = client.get(
                    origin + "/probe",
                    params={
                        "module": "yta_ready",
                        "target": "http://fixture:9000/" + path,
                    },
                )
                response.raise_for_status()
                line = next(
                    line for line in response.text.splitlines() if line.startswith("probe_success ")
                )
                assert float(line.split()[1]) == expected, path
                checks[path] = expected
        print(
            json.dumps(
                {
                    "rules": "passed",
                    "configuration": "passed",
                    "actual_http_probe_success": checks,
                    "notifications_sent": 0,
                    "deployment_qualification": "NOT_ASSESSED",
                }
            )
        )
    finally:
        for container in (probe, fixture):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=15)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=15)


if __name__ == "__main__":
    main()
