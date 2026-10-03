"""Experiment-container spawn lifecycle tests.

`run_experiment_container` shells out to docker like the research path; the
same fake-docker contract applies. The essential regression here: the
gateway hostname must be pinned via --add-host (gVisor containers cannot use
Docker's embedded DNS on the SG host — 2026-10-03), and an experiment
network without the gateway attached must fail fast instead of spawning a
container that cannot reach anything.
"""

from __future__ import annotations

import stat

import pytest

from youwei_runner.experiment_instance import (
    ExperimentInstanceError,
    ExperimentRunConfig,
    run_experiment_container,
)

_MEMBERS = (
    '{"id0": {"Name": "youwei-chat-litellm-1", "IPv4Address": "172.27.0.2/16"}}'
)
_INSPECT = (
    '{"youwei-research": {"Aliases": ["litellm"], "IPAddress": "172.27.0.2"}}'
)


def _config() -> ExperimentRunConfig:
    return ExperimentRunConfig(
        image="img:dev",
        gateway_url="http://litellm:4000/v1",
        gateway_host="litellm:4000",
        public_keys={"k1": "pub"},
        exec_config_version="v1",
        tool_base_url="http://sandbox-runner:8091",
        network="youwei-research",
    )


def _install_fake_docker(monkeypatch, tmp_path, *, members: str) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "run" ]; then\n'
        f'  echo "run $*" >> {tmp_path}/run.log\n'
        "  echo not-json\n"
        'elif [ "$1" = "rm" ]; then\n'
        f'  echo "rm $*" >> {tmp_path}/rm.log\n'
        "  exit 0\n"
        'elif [ "$1" = "network" ] && [ "$2" = "inspect" ]; then\n'
        f"  echo '{members}'\n"
        "  exit 0\n"
        'elif [ "$1" = "inspect" ]; then\n'
        f"  echo '{_INSPECT}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    import youwei_runner.experiment_instance as exp

    monkeypatch.setattr(exp, "_docker_binary", lambda: str(docker))


async def test_spawn_pins_resolved_gateway_via_add_host(monkeypatch, tmp_path):
    _install_fake_docker(monkeypatch, tmp_path, members=_MEMBERS)
    result = await run_experiment_container(_config(), request_json='{"x": 1}')
    assert result.ok is False
    run_log = (tmp_path / "run.log").read_text()
    assert "--add-host litellm:172.27.0.2" in run_log, (
        "experiment containers must reach the gateway without container-side "
        "DNS (runsc cannot resolve on this host)"
    )


async def test_gateway_not_on_network_fails_before_spawn(monkeypatch, tmp_path):
    """The gateway is not attached to the experiment network yet (Phase 1B
    enablement must attach it) — the spawn must fail fast with a clear error
    instead of a container that cannot reach anything."""
    _install_fake_docker(monkeypatch, tmp_path, members="{}")
    with pytest.raises(ExperimentInstanceError, match="not found on network"):
        await run_experiment_container(_config(), request_json='{"x": 1}')
    assert not (tmp_path / "run.log").exists(), (
        "no docker run may happen when the gateway cannot be resolved"
    )
