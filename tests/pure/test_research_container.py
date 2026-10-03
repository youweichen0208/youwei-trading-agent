"""Runner research-container lifecycle tests (S07 real-gateway regression).

`run_research_container` shells out to docker. These tests fake the docker
binary so the lifecycle contract is testable without a daemon:

- timeout kills the docker-run CLIENT only — the CONTAINER must be
  force-removed by name (regression: the 2026-10-02 real-gateway
  verification caught a timed-out research container still running and still
  calling the gateway after the Runner reported the timeout)
- a client fault (rm path) must also force-remove
- a normally-exited container (--rm semantics) is not force-removed
"""

from __future__ import annotations

import json
import os
import stat
import textwrap

import pytest

from youwei_runner.research import (
    ResearchExecutionError,
    ResearchRunConfig,
    run_research_container,
)


def _config(tmp_path, *, timeout_seconds: float) -> ResearchRunConfig:
    return ResearchRunConfig(
        image="img:dev",
        gateway_url="http://litellm:4000/v1",
        gateway_host="litellm:4000",
        public_keys={"k1": "pub"},
        exec_config_version="v1",
        timeout_seconds=timeout_seconds,
        network="youwei-research",
    )


def _install_fake_docker(
    monkeypatch, tmp_path, *, run_body: str, members: dict | None = None
) -> str:
    """Write a fake docker CLI; return its path. `run_body` is the shell body
    executed for `docker run` invocations (args land in run.log). `docker rm`
    invocations append their args to rm.log. `members` maps container name ->
    {"Aliases": [...], "IPAddress": ...} on the test network "youwei-research"
    and backs `docker network inspect` / `docker inspect` (the gateway
    hostname resolution the Runner performs through the docker socket)."""
    if members is None:
        members = {
            "youwei-chat-litellm-1": {
                "Aliases": ["litellm"],
                "IPAddress": "172.27.0.2",
            }
        }
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
        '#!/usr/bin/env bash\n'
        'if [ "$1" = "run" ]; then\n'
        f'  echo "run $*" >> {tmp_path}/run.log\n'
        f'  {run_body}\n'
        'elif [ "$1" = "rm" ]; then\n'
        f'  echo "rm $*" >> {tmp_path}/rm.log\n'
        '  exit 0\n'
        'elif [ "$1" = "network" ] && [ "$2" = "inspect" ]; then\n'
        f"  echo '{network_members}'\n"
        '  exit 0\n'
        'elif [ "$1" = "inspect" ]; then\n'
        f'{inspect_cases or "  :\n"}fi\n'
        'exit 0\n'
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    import youwei_runner.research as research

    monkeypatch.setattr(research, "_docker_binary", lambda: str(docker))
    return str(docker)


async def test_timeout_force_removes_the_container(monkeypatch, tmp_path):
    """A timed-out invocation must kill the docker-run client AND force-remove
    the container by name — killing only the client leaves the container
    running (it keeps calling the gateway)."""
    _install_fake_docker(
        monkeypatch, tmp_path,
        run_body=f"sleep 30",
    )
    with pytest.raises(ResearchExecutionError, match="exceeded"):
        await run_research_container(
            _config(tmp_path, timeout_seconds=0.5),
            request_json='{"x": 1}',
        )
    log = (tmp_path / "rm.log").read_text()
    assert "rm -f youwei-res-" in log, (
        "timeout path must force-remove the container by name"
    )


async def test_client_fault_force_removes_the_container(monkeypatch, tmp_path):
    """If the docker-run client dies without a clean exit, the container must
    be force-removed (no leaked slot)."""
    _install_fake_docker(
        monkeypatch, tmp_path,
        run_body="kill -9 $$",
    )
    # exit_code != 0 -> decoded as a failed result (not an exception), but the
    # container must still have been force-removed.
    result = await run_research_container(
        _config(tmp_path, timeout_seconds=10.0),
        request_json='{"x": 1}',
    )
    assert result.ok is False
    log = (tmp_path / "rm.log").read_text()
    assert "rm -f youwei-res-" in log


async def test_normal_exit_does_not_force_remove(monkeypatch, tmp_path):
    """A container that exits by itself (--rm semantics) is not force-removed
    (the rm path stays untouched)."""
    _install_fake_docker(
        monkeypatch, tmp_path,
        run_body="echo not-json",
    )
    result = await run_research_container(
        _config(tmp_path, timeout_seconds=10.0),
        request_json='{"x": 1}',
    )
    assert result.ok is False
    assert not (tmp_path / "rm.log").exists() or "rm -f" not in (
        tmp_path / "rm.log"
    ).read_text()


async def test_spawn_pins_resolved_gateway_via_add_host(monkeypatch, tmp_path):
    """gVisor (runsc) containers cannot use Docker's embedded DNS on the SG
    host (EAI_AGAIN for every name, 2026-10-03) — the Runner must resolve the
    gateway hostname through the docker socket and pin it into the container
    with --add-host (gVisor reads /etc/hosts). Regression for the S12
    rehearsal failure: research containers retried the gateway with
    APIConnectionError until the invocation failed."""
    _install_fake_docker(monkeypatch, tmp_path, run_body="echo not-json")
    result = await run_research_container(
        _config(tmp_path, timeout_seconds=10.0),
        request_json='{"x": 1}',
    )
    assert result.ok is False
    run_log = (tmp_path / "run.log").read_text()
    assert "--add-host litellm:172.27.0.2" in run_log, (
        "the spawned container must reach the gateway without container-side "
        "DNS (runsc cannot resolve on this host)"
    )


async def test_unresolvable_gateway_fails_before_spawn(monkeypatch, tmp_path):
    """A gateway hostname with no matching member on the target network must
    fail loudly BEFORE any container is spawned (honest fast failure instead
    of a spawned container that cannot reach anything)."""
    _install_fake_docker(monkeypatch, tmp_path, run_body="true", members={})
    with pytest.raises(ResearchExecutionError, match="not found on network"):
        await run_research_container(
            _config(tmp_path, timeout_seconds=10.0),
            request_json='{"x": 1}',
        )
    assert not (tmp_path / "run.log").exists(), (
        "no docker run may happen when the gateway cannot be resolved"
    )


async def test_gateway_and_image_required(monkeypatch, tmp_path):
    _install_fake_docker(monkeypatch, tmp_path, run_body="true")
    cfg = _config(tmp_path, timeout_seconds=1.0)
    with pytest.raises(ResearchExecutionError):
        await run_research_container(
            ResearchRunConfig(
                image="", gateway_url=cfg.gateway_url, gateway_host="h",
                public_keys={"k": "v"}, exec_config_version="v1",
            ),
            request_json="{}",
        )
    with pytest.raises(ResearchExecutionError):
        await run_research_container(
            ResearchRunConfig(
                image="i", gateway_url="", gateway_host="",
                public_keys={"k": "v"}, exec_config_version="v1",
            ),
            request_json="{}",
        )
