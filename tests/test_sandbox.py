"""S03a: sandbox execution and the artifact chain.

Acceptance mapped from the plan (S03) and architecture §10:
- the sandbox cannot reach the network, write the root filesystem,
  run as root, or see host environment secrets
- inputs are a materialized, hash-verified frozen snapshot mounted
  read-only; outputs live only in a size-capped tmpfs and reach the
  host as an in-memory-validated tar — a runaway writer is bounded
  by the tmpfs, not by host disk
- outputs are untrusted: symlink/hardlink members, disallowed
  extensions, oversized files and invalid UTF-8 are rejected
- timeout kills and removes the container; OOM kills are detected
- artifacts are stored only through the fenced attempt path and are
  append-only at the database level
- a failing script is a result (exit code), not an infrastructure
  failure
"""

import asyncio
import json
import shutil
import tempfile
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from youwei_core.data.snapshots import freeze_daily_bars
from youwei_core.jobs.service import RunSubmission, submit_run
from youwei_core.jobs.worker import claim_next_job, complete_attempt, fail_attempt
from youwei_core.sandbox.handler import make_sandbox_handler
from youwei_runner.execution import (
    ArtifactValidationError,
    SandboxConfig,
    SandboxExecutionError,
    materialize_snapshot,
    run_sandbox,
    verify_spool,
)
from youwei_core.sandbox.store import FencedArtifacts, get_artifact, store_artifacts
from test_data_pit import _ingest, _row, _security

RUNNER_SECRET = "test-runner-dedicated-secret-at-least-32"


@pytest.fixture(scope="module")
def remote_runner():
    """Real HTTP subprocess with only Runner config; no Core/provider secrets."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="youwei-runner-http-") as spool:
        env = {k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT") if k in os.environ}
        env.update(YOUWEI_RUNNER_SECRET=RUNNER_SECRET, YOUWEI_RUNNER_DEVELOPMENT="true",
                   YOUWEI_RUNNER_RUNTIME="", YOUWEI_RUNNER_SPOOL_ROOT=spool)
        launch = ("import uvicorn; from youwei_runner.app import create_app; "
                  "from youwei_runner.settings import RunnerSettings; "
                  f"uvicorn.run(create_app(RunnerSettings()), host='127.0.0.1', port={port}, log_level='error')")
        proc = subprocess.Popen([sys.executable, "-c", launch], env=env)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError("Runner subprocess exited during startup")
                try:
                    with urllib.request.urlopen(url + "/healthz", timeout=0.2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Runner did not start")
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _config(**overrides) -> SandboxConfig:
    defaults = {"timeout_seconds": 30.0}
    defaults.update(overrides)
    return SandboxConfig(**defaults)


async def _execute(script: str, *, config: SandboxConfig | None = None, argv=None, env=None):
    with tempfile.TemporaryDirectory(prefix="youwei-sbx-test-") as spool:
        inputs = Path(spool) / "inputs"
        inputs.mkdir()
        (inputs / "job_script.py").write_text(script)
        return await run_sandbox(
            config or _config(), inputs_dir=inputs, argv=argv, env=env
        )


async def _submit_and_claim(engine, tenant_id, payload, *, kind="sandbox.execute"):
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(kind=kind, jobs=[{"kind": kind, "payload": payload}]),
        idempotency_key=f"idem-{uuid.uuid4()}",
    )
    claimed = await claim_next_job(engine, "test-worker")
    assert claimed is not None and claimed.kind == kind
    return claimed


# --- isolation ---------------------------------------------------------------


async def test_sandbox_isolation_contract(db_engine, tenant_id):
    """Network blocked, read-only root, non-root uid, no host env."""
    script = """
import json, os, socket
result = {}
try:
    socket.create_connection(("93.184.216.34", 80), timeout=3)
    result["network"] = "OPEN"
except Exception as e:
    result["network"] = f"blocked ({type(e).__name__})"
try:
    with open("/etc/youwei_write_test", "w") as f:
        f.write("x")
    result["root_fs"] = "WRITABLE"
except Exception as e:
    result["root_fs"] = f"read-only ({type(e).__name__})"
result["uid"] = os.getuid()
result["saw_secret"] = "youwei_sbx_secret_marker" in os.environ
with open("/outputs/isolation.json", "w") as f:
    json.dump(result, f)
"""
    import os

    os.environ["youwei_sbx_secret_marker"] = "host-secret"
    try:
        result = await _execute(script)
    finally:
        del os.environ["youwei_sbx_secret_marker"]

    assert result.exit_code == 0
    report = json.loads(result.artifacts[0].content)
    assert report["network"].startswith("blocked")
    assert report["root_fs"].startswith("read-only")
    assert report["uid"] != 0
    assert report["saw_secret"] is False  # host env never forwarded


# --- execution + artifacts -----------------------------------------------------


async def test_handler_materializes_snapshot_and_stores_artifacts(db_engine, tenant_id, remote_runner):
    sec = await _security(db_engine)
    start, end = date(2026, 8, 1), date(2026, 8, 5)
    ingest = await _ingest(
        db_engine,
        {"AAPL": json.dumps([_row(f"2026-08-0{d}", 100.0 + d) for d in range(1, 6)])},
        "AAPL",
        sec,
        start,
        end,
    )
    snap = await freeze_daily_bars(
        db_engine, [sec], start, end, as_of=ingest.usable_at, mode="forward"
    )

    script = """
import json
bars = json.load(open("/inputs/snapshot/content.json"))
closes = [b["close"] for b in bars]
json.dump({"n": len(bars), "last": closes[-1]}, open("/outputs/summary.json", "w"))
"""
    claimed = await _submit_and_claim(
        db_engine,
        tenant_id,
        {"script": script, "snapshot_id": str(snap.snapshot_id)},
    )
    from youwei_core.sandbox.client import RunnerClient
    async with RunnerClient(remote_runner) as runner_client:
        handler = make_sandbox_handler(db_engine, runner_client, secret=RUNNER_SECRET)
        summary = await handler(claimed)
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)

    assert summary["exit_code"] == 0
    assert summary["snapshot"]["content_sha256"] == snap.content_sha256
    assert len(summary["artifacts"]) == 1

    art = summary["artifacts"][0]
    assert art["path"] == "summary.json"
    stored = await get_artifact(db_engine, tenant_id, uuid.UUID(summary["artifact_ids"][0]))
    assert stored["content"] == '{"n": 5, "last": 105.0}'
    assert stored["content_sha256"] == art["sha256"]
    assert stored["attempt_no"] == claimed.attempt_no
    assert stored["job_id"] == str(claimed.job_id)

    # the spool hash verified the snapshot bytes on write
    async with db_engine.begin() as conn:
        ev = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM events "
                    "WHERE event_type = 'sandbox.artifacts_stored'"
                )
            )
        ).scalar_one()
    assert ev == 1


async def test_script_failure_is_a_result_not_an_infra_failure(db_engine, tenant_id, remote_runner):
    claimed = await _submit_and_claim(
        db_engine, tenant_id, {"script": "import sys; sys.exit(3)"}
    )
    from youwei_core.sandbox.client import RunnerClient
    async with RunnerClient(remote_runner) as runner_client:
        handler = make_sandbox_handler(db_engine, runner_client, secret=RUNNER_SECRET)
        summary = await handler(claimed)
    assert summary["exit_code"] == 3
    assert summary["artifacts"] == []
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)


async def test_argv_and_env_passed_to_script(db_engine, tenant_id):
    script = """
import json, os
json.dump({"argv": __import__("sys").argv[1:], "env": os.environ.get("SBX_MODE")},
          open("/outputs/args.json", "w"))
"""
    result = await _execute(script, argv=["--check", "x y"], env={"SBX_MODE": "probe"})
    payload = json.loads(result.artifacts[0].content)
    assert payload["argv"] == ["--check", "x y"]  # shell-quoting safe
    assert payload["env"] == "probe"


# --- output validation ------------------------------------------------------------


async def test_symlink_output_rejected(db_engine, tenant_id):
    script = """
import os, json
json.dump({}, open("/outputs/real.json", "w"))
os.symlink("real.json", "/outputs/link.json")
"""
    with pytest.raises(ArtifactValidationError, match="symlink"):
        await _execute(script)


async def test_extension_size_and_utf8_limits(db_engine, tenant_id):
    # disallowed extension
    with pytest.raises(ArtifactValidationError, match="not allowed"):
        await _execute("open('/outputs/code.py','w').write('x = 1')")

    # per-file size cap
    config = _config(max_output_bytes=16)
    with pytest.raises(ArtifactValidationError, match="bytes > cap"):
        await _execute("open('/outputs/big.txt','w').write('x' * 64)", config=config)

    # invalid UTF-8
    with pytest.raises(ArtifactValidationError, match="UTF-8"):
        await _execute(
            "open('/outputs/bin.txt','wb').write(bytes([0xff, 0xfe, 0x00, 0x01]))"
        )


# --- resource limits ---------------------------------------------------------------


async def test_timeout_kills_and_removes_container(db_engine, tenant_id):
    with pytest.raises(SandboxExecutionError, match="exceeded"):
        await _execute("x = 0\nwhile True: x += 1", config=_config(timeout_seconds=3))

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker not available")
    proc = await asyncio.create_subprocess_exec(
        docker, "ps", "--filter", "name=youwei-sbx-", "--format", "{{.Names}}",
        stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    assert out.decode().strip() == ""  # nothing left behind


async def test_oom_kill_detected(db_engine, tenant_id):
    script = "data = []\nwhile True: data.append(bait := b'x' * 1024 * 1024)"
    with pytest.raises(SandboxExecutionError):
        await _execute(script, config=_config(memory="48m", timeout_seconds=60))


# --- spool and storage ----------------------------------------------------------------


async def test_spool_hash_verification_detects_tampering(db_engine):
    sec = await _security(db_engine)
    start, end = date(2026, 8, 1), date(2026, 8, 5)
    ingest = await _ingest(
        db_engine,
        {"AAPL": json.dumps([_row("2026-08-01", 100.0)])},
        "AAPL",
        sec,
        start,
        end,
    )
    snap = await freeze_daily_bars(
        db_engine, [sec], start, end, as_of=ingest.usable_at, mode="forward"
    )
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        from youwei_core.data.snapshots import read_snapshot
        from youwei_contracts.sandbox import SnapshotBundle
        frozen = await read_snapshot(db_engine, snap.snapshot_id)
        info = materialize_snapshot(SnapshotBundle(snapshot_id=snap.snapshot_id, content=frozen["content"], manifest=frozen["manifest"]), target)
        assert info["content_sha256"] == snap.content_sha256
        verify_spool(snap.content_sha256, target / "snapshot")

        # tamper with the spooled bytes: the hash no longer matches
        (target / "snapshot" / "content.json").write_text("[]")
        with pytest.raises(Exception, match="mismatch"):
            verify_spool(snap.content_sha256, target / "snapshot")


async def test_artifact_storage_is_fenced(db_engine, tenant_id):
    claimed = await _submit_and_claim(
        db_engine, tenant_id, {"script": "pass"}, kind="sandbox.execute"
    )
    # a newer attempt supersedes the first one
    await fail_attempt(db_engine, claimed.job_id, claimed.attempt_no, "retry")

    from youwei_contracts.sandbox import Artifact as CollectedArtifact

    stale = [
        CollectedArtifact(
            path="x.json", extension=".json", size=2,
            sha256=__import__("hashlib").sha256(b"{}").hexdigest(), content="{}",
        )
    ]
    with pytest.raises(FencedArtifacts):
        await store_artifacts(
            db_engine,
            tenant_id=tenant_id,
            run_id=claimed.run_id,
            job_id=claimed.job_id,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            collected=stale,
        )
    async with db_engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM artifacts"))).scalar_one()
    assert n == 0


async def test_artifacts_append_only(db_engine, tenant_id):
    claimed = await _submit_and_claim(
        db_engine, tenant_id, {"script": "pass"}, kind="sandbox.execute"
    )
    from youwei_contracts.sandbox import Artifact as CollectedArtifact

    await store_artifacts(
        db_engine,
        tenant_id=tenant_id,
        run_id=claimed.run_id,
        job_id=claimed.job_id,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        collected=[
            CollectedArtifact(
                path="x.json", extension=".json", size=2,
                sha256=__import__("hashlib").sha256(b"{}").hexdigest(), content="{}",
            )
        ],
    )
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM artifacts"))
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("UPDATE artifacts SET path = 'y.json'"))


async def test_worker_cancellation_reaches_independent_runner(db_engine, tenant_id, remote_runner):
    import httpx
    from datetime import timedelta
    from youwei_contracts.capability import sign_capability
    from youwei_contracts.sandbox import SandboxRequest, request_digest
    from youwei_core.config import Settings
    from youwei_core.jobs.service import cancel_run, get_run_view
    from youwei_core.sandbox.client import RunnerClient
    from youwei_core.worker.loop import WorkerLoop

    payload = {"script": "import time; time.sleep(30)"}
    run = await submit_run(db_engine, tenant_id,
        RunSubmission(kind="sandbox.execute",
                      jobs=[{"kind": "sandbox.execute", "payload": payload}]), str(uuid.uuid4()))
    entered = asyncio.Event()
    captured = {}
    async with RunnerClient(remote_runner) as runner_client:
        handler = make_sandbox_handler(db_engine, runner_client, secret=RUNNER_SECRET)
        async def observe(claimed):
            captured["job"] = claimed
            entered.set()
            return await handler(claimed)
        loop = WorkerLoop(db_engine, handlers={"sandbox.execute": observe},
                          settings=Settings(heartbeat_interval_seconds=0.1, lease_ttl_seconds=5))
        task = asyncio.create_task(loop.run_once())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            claimed = captured["job"]
            request = SandboxRequest(job_id=claimed.job_id, run_id=claimed.run_id,
                tenant_id=tenant_id, attempt_id=claimed.attempt_id, attempt_no=claimed.attempt_no, **payload)
            token = sign_capability(RUNNER_SECRET, job_id=claimed.job_id,
                tenant_id=tenant_id, attempt_no=claimed.attempt_no,
                scopes=("sandbox:execute", f"payload:{request_digest(request)}"),
                exp=datetime.now(UTC) + timedelta(seconds=5))
            async with httpx.AsyncClient(base_url=remote_runner) as http:
                path = f"/v1/executions/{claimed.job_id}/{claimed.attempt_no}"
                headers = {"Authorization": "Bearer " + token}
                for _ in range(100):
                    response = await http.get(path, headers=headers)
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(0.02)
                assert response.status_code == 200
                await cancel_run(db_engine, tenant_id, run.run_id)
                assert await asyncio.wait_for(asyncio.shield(task), 4) is True
                status = (await http.get(path, headers=headers)).json()
                assert status["status"] == "cancelled"
                assert (await get_run_view(db_engine, tenant_id, run.run_id))["status"] == "cancelled"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_snapshot_from_another_tenant_is_rejected_before_http(db_engine, tenant_id):
    from youwei_core.sandbox.client import RunnerClient
    from youwei_core.data.snapshots import freeze_daily_bars
    owner = await _submit_and_claim(db_engine, uuid.uuid4(), {"script": "pass"})
    snap = await freeze_daily_bars(db_engine, [], date(2026, 1, 1), date(2026, 1, 1),
        as_of=datetime.now(UTC), mode="historical_source", created_by_attempt=owner.attempt_id)
    intruder = await _submit_and_claim(db_engine, tenant_id,
                                      {"script": "pass", "snapshot_id": str(snap.snapshot_id)})
    async with RunnerClient("http://127.0.0.1:1") as runner_client:
        handler = make_sandbox_handler(db_engine, runner_client, secret=RUNNER_SECRET)
        with pytest.raises(FencedArtifacts, match="snapshot not authorized"):
            await handler(intruder)
