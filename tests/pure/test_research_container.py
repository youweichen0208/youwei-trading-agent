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


def _install_fake_docker(monkeypatch, tmp_path, *, run_body: str) -> str:
    """Write a fake docker CLI; return its path. `run_body` is the shell body
    executed for `docker run` invocations. `docker rm` invocations append
    their args to rm.log."""
    docker = tmp_path / "docker"
    docker.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "$1" = "run" ]; then
            {run_body}
            elif [ "$1" = "rm" ]; then
                echo "rm $*" >> {tmp_path}/rm.log
                exit 0
            fi
            exit 0
            """
        )
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
