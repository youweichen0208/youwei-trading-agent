import json
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from yta_alpha.jobs import run_worker
from yta_alpha.models import DataMode
from yta_alpha.worker_health import WorkerHealth, probe


def test_health_is_atomic_private_and_uses_wall_clock(tmp_path):
    path = tmp_path / "worker.json"
    health = WorkerHealth(path, "REPLAY", True)
    health.write("ready")
    body = json.loads(path.read_text())
    now = datetime.fromisoformat(body["observed_at"])
    assert abs((datetime.now(UTC) - now).total_seconds()) < 5
    assert path.stat().st_mode & 0o777 == 0o600
    assert probe(path, "REPLAY", 60, now=now)["status"] == "ready"
    assert probe(path, "LIVE_RESEARCH", 60, now=now)["reason"] == "mode_mismatch"
    for offset in (-1, 61):
        assert (
            probe(path, "REPLAY", 60, now=now + timedelta(seconds=offset))["reason"]
            == "stale_heartbeat"
        )
    health.write("stopped")
    assert probe(path, "REPLAY", 60)["reason"] == "worker_not_ready"
    assert list(tmp_path.iterdir()) == [path]
    health = WorkerHealth(path, "LIVE_RESEARCH", False)
    health.write("ready")
    assert probe(path, "LIVE_RESEARCH", 60)["reason"] == "provider_unavailable"


def test_probe_rejects_missing_corrupt_oversized_and_naive_time_without_echo(tmp_path):
    path = tmp_path / "worker.json"
    assert probe(path, "REPLAY", 60)["status"] == "not_ready"
    health = WorkerHealth(path, "REPLAY", True)
    health.write("ready")
    body = json.loads(path.read_text())
    for value in (
        "private-secret",
        "x" * 8193,
        "[]",
        json.dumps({**body, "observed_at": "2024-12-03T00:00:00"}),
        json.dumps({**body, "service": "other"}),
    ):
        path.write_text(value)
        result = probe(path, "REPLAY", 60)
        assert result["status"] == "not_ready"
        assert "private-secret" not in json.dumps(result)


def test_worker_lifecycle_health_and_interruptible_wait(tmp_path, capsys):
    path = tmp_path / "worker.json"
    health = WorkerHealth(path, "REPLAY", True)
    stop = Event()
    logical_time = datetime(2024, 12, 3, tzinfo=UTC)
    calls = []

    def tick(now, **kwargs):
        assert json.loads(path.read_text())["state"] == "starting"
        calls.append(now)
        stop.set()
        return []

    run_worker(
        SimpleNamespace(mode=DataMode.REPLAY, tick=tick, queue_status=lambda *a, **k: {}),
        watch=True,
        interval=600,
        limit=100,
        owner=None,
        replay_at=logical_time,
        stop=stop,
        health=health,
    )
    assert calls == [logical_time]
    assert json.loads(path.read_text())["state"] == "stopped"
    assert json.loads(capsys.readouterr().out)["results"] == []


def test_batch_failure_marks_health_failed(tmp_path):
    path = tmp_path / "worker.json"

    def tick(*args, **kwargs):
        raise RuntimeError("private-provider-secret")

    with pytest.raises(RuntimeError):
        run_worker(
            SimpleNamespace(mode=DataMode.REPLAY, tick=tick, queue_status=lambda *a, **k: {}),
            watch=False,
            interval=1,
            limit=100,
            owner=None,
            replay_at=None,
            stop=Event(),
            health=WorkerHealth(path, "REPLAY", True),
        )
    assert json.loads(path.read_text())["state"] == "failed"
    assert "private-provider-secret" not in path.read_text()


def test_probe_cli_exit_codes_and_safe_output(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "worker.json"
    command = [
        sys.executable,
        "-m",
        "yta_alpha.worker_health",
        "--file",
        str(path),
        "--expect-mode",
        "REPLAY",
    ]
    missing = subprocess.run(command, capture_output=True, check=False, timeout=5)
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["reason"] == "probe_failed"
    WorkerHealth(path, "REPLAY", True).write("ready")
    ready = subprocess.run(command, capture_output=True, check=False, timeout=5)
    assert ready.returncode == 0
    assert json.loads(ready.stdout)["status"] == "ready"


def test_paused_watch_reports_state_without_dispatch_and_stops_cleanly(tmp_path, capsys):
    stop = Event()
    path = tmp_path / "worker.json"
    states = []

    class Health(WorkerHealth):
        def write(self, state):
            super().write(state)
            states.append(state)
            if state == "paused":
                assert probe(path, "REPLAY", 60)["reason"] == "dispatch_paused"
                stop.set()

    def forbidden(*args, **kwargs):
        pytest.fail("Paused watch must not call tick")

    run_worker(
        SimpleNamespace(
            mode=DataMode.REPLAY, tick=forbidden, queue_status=lambda *a, **k: {"due_count": 2}
        ),
        watch=True,
        interval=600,
        limit=100,
        owner=None,
        replay_at=None,
        stop=stop,
        health=Health(path, "REPLAY", True),
        dispatch_enabled=False,
    )
    assert states == ["starting", "paused", "stopped"]
    assert json.loads(capsys.readouterr().out) == {
        "data_mode": "REPLAY",
        "dispatch_status": "PAUSED",
        "results": [],
        "queue": {"due_count": 2},
    }
