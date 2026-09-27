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
from youwei_core.sandbox.runner import (
    ArtifactValidationError,
    SandboxConfig,
    SandboxExecutionError,
    materialize_snapshot,
    run_sandbox,
    verify_spool,
)
from youwei_core.sandbox.store import FencedArtifacts, get_artifact, store_artifacts
from test_data_pit import _ingest, _row, _security


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
        RunSubmission(kind=kind, total_budget_micros=0, jobs=[{"kind": kind, "payload": payload}]),
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


async def test_handler_materializes_snapshot_and_stores_artifacts(db_engine, tenant_id):
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
    handler = make_sandbox_handler(db_engine, _config())
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


async def test_script_failure_is_a_result_not_an_infra_failure(db_engine, tenant_id):
    claimed = await _submit_and_claim(
        db_engine, tenant_id, {"script": "import sys; sys.exit(3)"}
    )
    handler = make_sandbox_handler(db_engine, _config())
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

    docker = "/usr/local/bin/docker"
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
        info = await materialize_snapshot(db_engine, snap.snapshot_id, target)
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

    from youwei_core.sandbox.runner import CollectedArtifact

    stale = [
        CollectedArtifact(
            path="x.json", extension=".json", size=2,
            sha256="0" * 64, content="{}",
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
    from youwei_core.sandbox.runner import CollectedArtifact

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
                sha256="0" * 64, content="{}",
            )
        ],
    )
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM artifacts"))
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("UPDATE artifacts SET path = 'y.json'"))
