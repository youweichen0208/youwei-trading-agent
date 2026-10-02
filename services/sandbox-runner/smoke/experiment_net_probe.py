#!/usr/bin/env python3
"""S08 network probe: runs INSIDE a container on the youwei-experiment network.

Verifies the D1=A topology for the experiment instance (owner decision
2026-10-02): the experiment container may reach EXACTLY {the approved
gateway, this Runner's tool endpoint} — nothing else:

  1. Docker DNS resolves the gateway and Runner aliases on this network.
  2. The Runner's tool endpoint answers (GET /healthz on RUNNER_ADDR).
  3. The gateway answers on GATEWAY_ADDR (any HTTP response proves
     connectivity; the mock may answer 501 to GET).
  4. External egress is blocked (no NAT on the internal network).
  5. The docker socket gateway (172.17.0.1:2375) and the host's public IP
     are unreachable.
  6. Cross-network isolation: a member of the research network
     (RESEARCH_NET_ADDR) is NOT reachable from here — and by symmetry the
     research containers cannot reach this Runner (probed separately from
     the research side).

Env: RUNNER_ADDR (host:port, required), GATEWAY_ADDR (host:port, required),
RESEARCH_NET_ADDR (host:port, optional), EXTRA_ISOLATION_ADDRS (comma
separated host:port, optional). Prints a JSON verdict per check; exit 0 iff
the required checks pass.

Run (host side):
  docker run --rm --network youwei-experiment \
      -v <checkout>/services/sandbox-runner/smoke:/smoke:ro \
      -e RUNNER_ADDR=runner:8091 -e GATEWAY_ADDR=mock-gateway:9901 \
      --entrypoint python3 youwei/agent-runtime:s08c /smoke/experiment_net_probe.py
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
    print(json.dumps(results[-1], sort_keys=True), flush=True)


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 80, proto=socket.IPPROTO_TCP)
        return sorted({i[4][0] for i in infos})
    except OSError:
        return []


def _http_reaches(addr: str) -> tuple[bool, str]:
    """Any HTTP response (any status) proves connectivity."""
    url = urllib.parse.urlparse(f"http://{addr}")
    try:
        conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=10)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        body = resp.read(200).decode("utf-8", "replace")
        conn.close()
        return True, f"HTTP {resp.status} {body[:80]}"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — malformed responses still prove TCP
        return True, f"protocol-level response ({type(exc).__name__})"


def _unreachable(addr: str, name: str) -> None:
    try:
        url = urllib.parse.urlparse(f"http://{addr}")
        with socket.create_connection(
            (url.hostname, url.port or 80), timeout=5
        ):
            record(name, False, f"CONNECTED to {addr} (must be blocked!)")
    except OSError as exc:
        record(name, True, f"blocked ({type(exc).__name__})")


def main() -> int:
    runner_addr = os.environ.get("RUNNER_ADDR", "").strip()
    gateway_addr = os.environ.get("GATEWAY_ADDR", "").strip()
    if not runner_addr or not gateway_addr:
        print("FATAL: RUNNER_ADDR and GATEWAY_ADDR are required", file=sys.stderr)
        return 2

    runner_host = runner_addr.rsplit(":", 1)[0]
    gateway_host = gateway_addr.rsplit(":", 1)[0]

    runner_ips = _resolve(runner_host)
    record(
        "dns_runner", bool(runner_ips),
        f"resolved {runner_ips}" if runner_ips else f"cannot resolve {runner_host}",
    )
    gateway_ips = _resolve(gateway_host)
    record(
        "dns_gateway", bool(gateway_ips),
        f"resolved {gateway_ips}" if gateway_ips else f"cannot resolve {gateway_host}",
    )

    ok, detail = _http_reaches(runner_addr)
    record("runner_tool_endpoint", ok, detail)
    ok, detail = _http_reaches(gateway_addr)
    record("gateway_reachable", ok, detail)

    for target in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.create_connection((target, 53), timeout=5):
                record(f"external_egress_{target}", False, "CONNECTED (must be blocked!)")
        except OSError as exc:
            record(f"external_egress_{target}", True, f"blocked ({type(exc).__name__})")

    _unreachable("172.17.0.1:2375", "docker_socket_gateway")
    _unreachable("168.144.39.34:80", "host_public_ip")

    research_addr = os.environ.get("RESEARCH_NET_ADDR", "").strip()
    if research_addr:
        _unreachable(research_addr, "research_network_isolation")

    for addr in [
        a.strip() for a in os.environ.get("EXTRA_ISOLATION_ADDRS", "").split(",") if a.strip()
    ]:
        _unreachable(addr, f"isolation_{addr}")

    required = [r for r in results if r["ok"] is not None]
    required_ok = all(r["ok"] is True for r in required)
    print("NET_PROBE OK" if required_ok else "NET_PROBE FAILED", flush=True)
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
