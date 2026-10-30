"""Read-only checks for compose.app-local.yaml; requires the built stack already running."""

import json
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import urlopen

COMPOSE = ["docker", "compose", "-f", "infra/compose/compose.app-local.yaml"]


def command(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def get(path):
    try:
        response = urlopen("http://127.0.0.1:3300" + path, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(1_000_000)


def main():
    for service in ("yta-web", "yta-bff", "alpha-engine"):
        container = command(*COMPOSE, "ps", "-q", service)
        assert container
        user = command("docker", "inspect", "--format", "{{.Config.User}}", container)
        assert user in ("node", "10001:10001")
        assert (
            command("docker", "inspect", "--format", "{{.HostConfig.ReadonlyRootfs}}", container)
            == "true"
        )
        caps = json.loads(
            command("docker", "inspect", "--format", "{{json .HostConfig.CapDrop}}", container)
        )
        assert "ALL" in caps
        ports = json.loads(
            command("docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}", container)
        )
        if service != "yta-web":
            assert not ports
        else:
            assert ports == {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3300"}]}
    code, body = get("/api/yta/v1/ready")
    assert code == 200 and json.loads(body)["data_mode"] == "REPLAY"
    assert get("/api/yta/v1/me")[0] == 401
    for resource in ("", "/openapi.json", "/init.js", "/swagger-ui.css", "/swagger-ui-bundle.js"):
        assert get("/api/yta/v1/docs" + resource)[0] == 401
    code, body = get("/login")
    assert code == 200
    asset = re.search(r'(?:src|href)="(/_next/static/[^\"]+)"', body.decode())
    assert asset and get(asset[1])[0] == 200
    # Web has no service/DB credentials and cannot resolve private backend service names.
    script = """
const fs = require('node:fs');
const dns = require('node:dns').promises;
(async () => {
  if (process.env.YTA_ALPHA_TOKEN || process.env.YTA_DATABASE_URL
      || fs.existsSync('/app/.env') || fs.existsSync('/app/.local')) process.exit(1);
  for (const host of ['alpha-engine', 'postgres']) {
    try { await dns.lookup(host); process.exit(1); }
    catch (e) { if (e.code !== 'ENOTFOUND') throw e; }
  }
})().catch(() => process.exit(1));
"""
    command(*COMPOSE, "exec", "-T", "yta-web", "node", "-e", script)
    symbols = """
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
base = 'http://127.0.0.1:8100/internal/v1/symbols/'
def request(symbol, authenticated):
    headers = {'Authorization': 'Bearer ' + os.environ['YTA_ALPHA_TOKEN']} if authenticated else {}
    try:
        response = urlopen(Request(base + symbol, headers=headers), timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)
assert request('NVDA', False)[0] == 401
status, history = request('nvda', True)
assert status == 200 and history['symbol'] == 'NVDA'
assert history['qualification_status'] == 'UNVERIFIED'
assert history['versions'][0]['source_id'] == 'yta:mvp-scope'
assert history['versions'][0]['effective_from'] is None
assert request('AAPL', True)[0] == 422
"""
    command(*COMPOSE, "exec", "-T", "alpha-engine", "python", "-c", symbols)
    queue = json.loads(
        command(
            *COMPOSE,
            "run",
            "--rm",
            "--no-deps",
            "jobs",
            "--queue-only",
            "--replay-at",
            "2024-12-03T00:00:00Z",
        )
    )
    assert queue["data_mode"] == "REPLAY" and queue["schema_version"] == 1
    assert queue["due_count"] == queue["eligible_count"] + queue["backoff_count"]
    print(
        "Container smoke passed: private/non-root/read-only services, "
        "assets, auth/ready, symbol history, worker"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Container smoke failed; inspect local service health (details not echoed)"
        ) from None
