#!/usr/bin/env python3
"""S07 network probe: runs INSIDE a container on the youwei-research network.

Verifies the restricted-egress topology once the REAL gateway is attached
(the S07m leftover: DNS / reachability with the production wiring):

  1. Docker DNS resolves the `litellm` alias on this network (record v4/v6).
  2. The gateway data plane is reachable and authorizes the research key.
  3. External egress is blocked (no NAT on the internal network).
  4. External DNS resolution behavior is recorded (observation, not pass/fail:
     the embedded resolver may or may not forward on an --internal network).
  5. OTHER chat-stack services (postgres / openwebui container IPs, passed via
     env) are NOT reachable from this network (isolation).

The research key is read from stdin (first line) so it never appears in the
docker command line or logs. Env (optional): CHAT_PG_ADDR, CHAT_WEBUI_ADDR
(host:port of the chat postgres / openwebui containers, for the isolation
checks). Prints a JSON verdict per check; exit 0 iff the required checks pass.

Run (host side):
  docker run --rm -i --network youwei-research \
      -v <checkout>/services/sandbox-runner/smoke:/smoke:ro \
      -e CHAT_PG_ADDR=... -e CHAT_WEBUI_ADDR=... \
      --entrypoint python3 \
      ghcr.io/youweichen0208/youwei-agent-runtime@sha256:998f060e... \
      /smoke/research_net_probe.py <<< "$RESEARCH_KEY"
"""

import http.client
import json
import os
import socket
import sys
import urllib.parse

results = []


def record(name: str, ok: bool | None, detail: str) -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(json.dumps(results[-1], sort_keys=True))


def dns_litellm():
    try:
        infos = socket.getaddrinfo("litellm", 4000, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        record("dns_litellm", True, f"resolved {addrs}")
        return addrs
    except OSError as exc:
        record("dns_litellm", False, str(exc))
        return []


def gateway_models(key: str):
    try:
        conn = http.client.HTTPConnection("litellm", 4000, timeout=15)
        conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {key}"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        conn.close()
        if resp.status == 200:
            ids = [m.get("id") for m in json.loads(raw).get("data", [])]
            record("gateway_models", True, f"status=200 models={ids}")
            return True
        record("gateway_models", False, f"status={resp.status} body={raw[:200]}")
        return False
    except OSError as exc:
        record("gateway_models", False, str(exc))
        return False


def external_egress():
    for target in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.create_connection((target, 53), timeout=5):
                record(f"external_egress_{target}", False, "CONNECTED (must be blocked!)")
                return
        except OSError as exc:
            record(f"external_egress_{target}", True, f"blocked ({type(exc).__name__})")


def external_dns():
    try:
        infos = socket.getaddrinfo("example.com", 80, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        record("external_dns_example.com", None,
               f"resolved {addrs[:2]} (observation: resolver behavior on internal network)")
    except OSError as exc:
        record("external_dns_example.com", None,
               f"failed ({type(exc).__name__}) (observation)")


def _unreachable(addr: str) -> None:
    try:
        host, port = addr.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=5):
            record(f"isolation_{addr}", False, "CONNECTED (must be blocked!)")
    except OSError as exc:
        record(f"isolation_{addr}", True, f"blocked ({type(exc).__name__})")


def main() -> int:
    key = sys.stdin.readline().strip()
    if not key:
        print("FATAL: research key expected on stdin", file=sys.stderr)
        return 2
    dns_litellm()
    models_ok = gateway_models(key)
    external_egress()
    external_dns()
    for name in ("CHAT_PG_ADDR", "CHAT_WEBUI_ADDR"):
        addr = os.environ.get(name, "").strip()
        if addr:
            _unreachable(addr)
    required = [r for r in results if r["ok"] is not None]
    required_ok = all(r["ok"] is True for r in required)
    print("NET_PROBE OK" if required_ok else "NET_PROBE FAILED")
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
