"""netresolve unit tests — the docker-socket gateway hostname mapping.

Context (2026-10-03, S12 rollout rehearsal): gVisor (runsc) on the SG host
cannot use Docker's embedded DNS (EAI_AGAIN for every name), so the Runner
resolves the gateway hostname through the docker socket and pins it into the
spawned container with ``--add-host``. These tests fake the docker CLI the
same way the lifecycle tests do.
"""

from __future__ import annotations

import json
import stat

import pytest

from youwei_runner.netresolve import (
    NetResolveError,
    gateway_add_host,
    gateway_hostname,
    resolve_network_hostname,
)


def _fake_docker(tmp_path, monkeypatch, *, members: dict) -> str:
    network_members = json.dumps(
        {
            f"id{i}": {"Name": name, "IPv4Address": f"{att['IPAddress']}/16"}
            for i, (name, att) in enumerate(members.items())
        }
    )
    inspect_cases = ""
    for name, att in members.items():
        payload = json.dumps({"youwei-research": att})
        inspect_cases += (
            f'if [ "$2" = "{name}" ]; then echo \'{payload}\'; exit 0; fi\n'
        )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "network" ] && [ "$2" = "inspect" ]; then\n'
        f"  echo '{network_members}'\n"
        "  exit 0\n"
        'elif [ "$1" = "inspect" ]; then\n'
        f'{inspect_cases or "  :\n"}fi\n'
        "exit 0\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    import youwei_runner.netresolve as netresolve

    monkeypatch.setattr(netresolve, "_sh", _fake_sh(str(docker)))
    return str(docker)


def _fake_sh(docker_path: str):
    async def sh(cmd: list[str]) -> tuple[int, str, str]:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            docker_path, *cmd[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode or 0, out.decode(), err.decode()

    return sh


async def test_resolves_network_alias(monkeypatch, tmp_path):
    _fake_docker(
        tmp_path, monkeypatch,
        members={
            "youwei-chat-litellm-1": {"Aliases": ["litellm"], "IPAddress": "172.27.0.2"},
        },
    )
    ip = await resolve_network_hostname("docker", "youwei-research", "litellm")
    assert ip == "172.27.0.2"


async def test_resolves_container_name(monkeypatch, tmp_path):
    """Docker's embedded DNS answers container names too — the mapping must
    match them as well (a deployment that registers the gateway by container
    name instead of an alias)."""
    _fake_docker(
        tmp_path, monkeypatch,
        members={
            "gateway-1": {"Aliases": [], "IPAddress": "10.0.0.9"},
        },
    )
    ip = await resolve_network_hostname("docker", "youwei-research", "gateway-1")
    assert ip == "10.0.0.9"


async def test_unknown_hostname_raises(monkeypatch, tmp_path):
    _fake_docker(tmp_path, monkeypatch, members={})
    with pytest.raises(NetResolveError, match="not found on network"):
        await resolve_network_hostname("docker", "youwei-research", "litellm")


async def test_gateway_add_host_args(monkeypatch, tmp_path):
    _fake_docker(
        tmp_path, monkeypatch,
        members={
            "youwei-chat-litellm-1": {"Aliases": ["litellm"], "IPAddress": "172.27.0.2"},
        },
    )
    assert await gateway_add_host(
        "docker", "http://litellm:4000/v1", "youwei-research"
    ) == ["--add-host", "litellm:172.27.0.2"]


async def test_ip_literal_gateway_url_needs_no_resolution(monkeypatch, tmp_path):
    """A gateway URL that is already an IP literal must not trigger any
    docker call (nothing to pin) and must not fail."""
    calls: list[list[str]] = []

    async def sh(cmd):
        calls.append(cmd)
        return 0, "{}", ""

    import youwei_runner.netresolve as netresolve

    monkeypatch.setattr(netresolve, "_sh", sh)
    assert await gateway_add_host(
        "docker", "http://172.27.0.2:4000/v1", "youwei-research"
    ) == []
    assert calls == []


def test_gateway_hostname_shapes():
    assert gateway_hostname("http://litellm:4000/v1") == "litellm"
    assert gateway_hostname("http://172.27.0.2:4000/v1") == ""
    assert gateway_hostname("") == ""
    assert gateway_hostname("not-a-url") == ""
