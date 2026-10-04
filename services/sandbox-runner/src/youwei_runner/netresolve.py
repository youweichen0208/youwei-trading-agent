"""Gateway hostname resolution for spawned agent-runtime containers.

Why this exists (2026-10-03, caught by the S12 rollout rehearsal): gVisor
(runsc) on the SG host cannot use Docker's embedded DNS — every lookup
returns EAI_AGAIN (internal and external names alike; verified with the
pinned alpine base image under ``--runtime runsc``, while the default
runtime resolves fine). The Runner enforces runsc in production, so spawned
research/experiment containers could not resolve the gateway hostname and
every model call failed with ``APIConnectionError`` (the agent retried with
backoff until the invocation failed).

The Runner is deliberately NOT attached to the research network (spawned
containers must not gain a network path to the Runner's control port), so it
cannot resolve the name through its own resolver either. Instead the Runner
maps the hostname through the Docker socket it already holds —
``docker network inspect`` + ``docker inspect`` — matching the same names
Docker's embedded DNS would answer (network aliases and container names,
scoped to the target network), and pins the result into the container via
``--add-host`` (gVisor reads /etc/hosts). Resolution happens per spawn, so a
gateway rebuild with a new IP is picked up on the next turn.

Known limitations:
- IPv4 only (the Docker bridge networks here are IPv4; an IPv6-only
  deployment would need to extend this).
- The experiment path resolves on the experiment network; the gateway is not
  attached there yet (Phase 1B enablement must attach it — until then
  experiment spawns fail fast with a clear error instead of DNS timeouts).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from urllib.parse import urlsplit


class NetResolveError(Exception):
    """The gateway hostname could not be mapped on the target network."""


async def _sh(cmd: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    return (
        proc.returncode or 0,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


def gateway_hostname(gateway_url: str) -> str:
    """Hostname part of the gateway URL ("" when absent or an IP literal —
    an IP literal needs no name pinning)."""
    try:
        host = urlsplit(gateway_url).hostname or ""
    except ValueError:
        return ""
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return ""


async def resolve_network_hostname(
    docker: str, network: str, hostname: str
) -> str:
    """Map ``hostname`` to an IPv4 address on ``network`` via the Docker
    socket (network aliases and container names — the same names Docker's
    embedded DNS would answer, scoped to the network)."""
    code, out, err = await _sh(
        [docker, "network", "inspect", network, "--format", "{{json .Containers}}"]
    )
    if code != 0:
        raise NetResolveError(
            f"docker network inspect {network!r} failed: {err.strip()[:200]}"
        )
    try:
        members = json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise NetResolveError(
            f"docker network inspect {network!r}: unparsable output"
        ) from exc
    if not isinstance(members, dict):
        raise NetResolveError(
            f"docker network inspect {network!r}: unexpected output shape"
        )
    for info in members.values():
        name = (info or {}).get("Name") or ""
        if not name:
            continue
        code, out, _ = await _sh(
            [docker, "inspect", name, "--format", "{{json .NetworkSettings.Networks}}"]
        )
        if code != 0:
            continue
        try:
            net = (json.loads(out or "{}") or {}).get(network) or {}
        except json.JSONDecodeError:
            continue
        aliases = net.get("Aliases") or []
        if hostname in aliases or name == hostname:
            ip = (net.get("IPAddress") or "").strip()
            if ip:
                return ip
    raise NetResolveError(
        f"gateway host {hostname!r} not found on network {network!r}"
    )


async def gateway_add_host(docker: str, gateway_url: str, network: str) -> list[str]:
    """``--add-host`` args pinning the gateway hostname into the spawned
    container (empty when the gateway URL is an IP literal — nothing to
    resolve)."""
    host = gateway_hostname(gateway_url)
    if not host:
        return []
    ip = await resolve_network_hostname(docker, network, host)
    return ["--add-host", f"{host}:{ip}"]
