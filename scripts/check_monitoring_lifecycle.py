"""Exercise real Prometheus alert transitions against isolated synthetic targets."""

import json
import subprocess
import time
from uuid import uuid4

import httpx
from check_monitoring import BLACKBOX, PROMETHEUS, ROOT, docker

FIXTURE = """from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
class Handler(BaseHTTPRequestHandler):
 failed = False
 def do_GET(self):
  if self.path.startswith('/control/'):
   Handler.failed = self.path.endswith('/fail')
   self.send_response(200)
   self.end_headers()
   return
  self.send_response(503 if Handler.failed else 200)
  self.send_header('Content-Type','application/json')
  self.end_headers()
  self.wfile.write(b'{"status":"not_ready"}' if Handler.failed else b'{"status":"ready"}')
 def log_message(self,*args): pass
for port in (3000,3100):
 Thread(target=HTTPServer(('0.0.0.0',port),Handler).serve_forever,daemon=True).start()
Thread().join() if False else __import__('threading').Event().wait()
"""


def main():
    prefix = "yta-alert-check-" + uuid4().hex[:10]
    network, fixture, probe, prometheus = (
        prefix,
        prefix + "-fixture",
        prefix + "-probe",
        prefix + "-prometheus",
    )
    directory = str(ROOT / "infra/monitoring")
    phases = []
    started = time.monotonic()

    def record(phase):
        phases.append({"phase": phase, "elapsed_seconds": round(time.monotonic() - started, 2)})
        print(json.dumps(phases[-1]), flush=True)

    def container(name, *args):
        return docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            *args,
        )

    def control(action):
        docker(
            "exec",
            fixture,
            "python",
            "-c",
            "import urllib.request; urllib.request.urlopen("
            + repr("http://127.0.0.1:3100/control/" + action)
            + ",timeout=3).read()",
        )

    try:
        docker("network", "create", network)
        container(
            fixture,
            "--network-alias",
            "yta-bff",
            "--network-alias",
            "yta-web",
            "--entrypoint",
            "python",
            "yta-alpha:local",
            "-c",
            FIXTURE,
        )
        container(
            probe,
            "--network-alias",
            "blackbox",
            "-v",
            directory + ":/config:ro",
            BLACKBOX,
            "--config.file=/config/blackbox.yaml",
        )
        container(
            prometheus,
            "-v",
            directory + ":/etc/prometheus:ro",
            "--tmpfs",
            "/prometheus:rw,mode=1777,size=128m",
            "-p",
            "127.0.0.1::9090",
            PROMETHEUS,
            "--config.file=/etc/prometheus/prometheus.yaml",
            "--storage.tsdb.path=/prometheus",
        )
        ports = json.loads(
            docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", prometheus)
        )
        origin = "http://127.0.0.1:" + ports["9090/tcp"][0]["HostPort"]
        with httpx.Client(trust_env=False, timeout=5) as client:

            def query(expression):
                response = client.get(origin + "/api/v1/query", params={"query": expression})
                response.raise_for_status()
                body = response.json()
                assert body["status"] == "success"
                return body["data"]["result"]

            def wait_for(predicate, seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    try:
                        if predicate():
                            return
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                raise RuntimeError("Monitoring transition did not occur within deadline")

            def healthy():
                return len(query('probe_success{job="yta-readiness"} == 1')) == 2 and not query(
                    'ALERTS{alertname=~"YTA.*"}'
                )

            def alert_state(state):
                return (
                    len(query('ALERTS{alertname="YTAServiceNotReady",alertstate="' + state + '"}'))
                    == 2
                )

            wait_for(healthy, 45)
            record("healthy")
            control("fail")
            wait_for(lambda: alert_state("pending"), 45)
            record("pending")
            assert not alert_state("firing")
            wait_for(lambda: alert_state("firing"), 165)
            record("firing")
            assert phases[-1]["elapsed_seconds"] - phases[-2]["elapsed_seconds"] >= 110
            control("recover")
            wait_for(healthy, 45)
            record("resolved")
        print(
            json.dumps(
                {
                    "phases": phases,
                    "real_prometheus": True,
                    "synthetic_targets": True,
                    "notifications_sent": 0,
                    "deployment_qualification": "NOT_ASSESSED",
                }
            ),
            flush=True,
        )
    finally:
        for name in (prometheus, probe, fixture):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=15)


if __name__ == "__main__":
    main()
