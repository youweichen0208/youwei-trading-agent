"""Sandbox runner (S03, architecture §10): execute untrusted code in a
restricted container and collect validated artifacts.

Isolation contract (all enforced by the runner, never by the caller):
- fixed image from runner config — the job cannot choose an image,
  mount, network mode or host path
- non-root user, read-only root filesystem, cap-drop ALL,
  no-new-privileges, CPU/memory/PID limits, no network, no secrets:
  the container receives ONLY the explicit per-job env vars
- inputs (a materialized frozen snapshot + the job script) mount
  read-only
- outputs go to a size-capped tmpfs — the ONLY host-writable path is
  none: the container runs detached, a trusted wrapper marks
  completion, and outputs are copied out while the container (and
  its tmpfs) is still alive. A runaway writer is therefore bounded
  by the tmpfs size, not by host disk (S03 acceptance: 磁盘满不拖垮)
- wall-clock timeout kills and removes the container; an OOM-killed
  script is distinguished from a timeout via container state

Artifact validation (outputs are untrusted input): whitelist
extensions, per-file and total size caps, file-count cap, reject
symlinks / hardlinks / special files / path traversal, strict UTF-8.
Nothing here ever executes, unpickles or template-renders an output.

The runner talks to the local Docker socket only. On the target host
the runtime is configured as gVisor (runsc, S01-verified); the dev
and test environments use the default runtime — the isolation flags
above are identical either way.
"""

import asyncio
import hashlib
import io
import json
import os
import shlex
import stat
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def _docker_binary() -> str:
    import shutil as _shutil

    for cand in (_shutil.which("docker"), "/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("docker binary not found; the sandbox runner requires Docker")


class SandboxExecutionError(Exception):
    """Infrastructure-level failure: timeout, OOM kill or runner
    error — distinct from the script's own exit code."""


class ArtifactValidationError(Exception):
    pass


class SpoolError(Exception):
    pass


EXIT_CODE_MARKER = "_exit_code"
POLL_INTERVAL_SECONDS = 0.2
IDLE_SLEEP_SECONDS = 600  # keeps the tmpfs alive for the copy-out


@dataclass
class SandboxConfig:
    # image comes from runner configuration only (deployment pins the
    # digest); jobs never specify it
    image: str = "python:3.13-alpine"
    memory: str = "256m"
    cpus: str = "0.5"
    pids_limit: int = 64
    timeout_seconds: float = 30.0
    outputs_tmpfs_size: str = "64m"
    tmp_tmpfs_size: str = "16m"
    max_output_files: int = 50
    max_output_bytes: int = 16 * 1024 * 1024
    max_total_output_bytes: int = 64 * 1024 * 1024
    allowed_extensions: tuple[str, ...] = (".json", ".csv", ".txt", ".md")
    log_limit: int = 65536
    # e.g. "runsc" on the target host (gVisor); empty = default runtime
    runtime: str = ""
    sandbox_uid: int = 65534
    sandbox_gid: int = 65534


@dataclass
class CollectedArtifact:
    path: str  # relative POSIX path inside /outputs
    extension: str
    size: int
    sha256: str
    content: str


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    artifacts: list[CollectedArtifact] = field(default_factory=list)
    duration_seconds: float = 0.0


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated at {limit} bytes]"


def _validate_tar(config: SandboxConfig, tar_bytes: bytes) -> list[CollectedArtifact]:
    """Validate the tar stream of /outputs IN MEMORY — outputs never
    touch the host filesystem. All-or-nothing: one violation rejects
    the whole batch. Only plain whitelisted text files within the caps
    are accepted; symlinks, hardlinks, special members, absolute or
    traversing paths are rejected before any content is read."""
    collected: list[CollectedArtifact] = []
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
    except tarfile.TarError as exc:
        raise ArtifactValidationError(f"outputs are not a readable tar: {exc}") from None
    with archive:
        for member in archive:
            name = member.name.lstrip("./")
            if name in ("", EXIT_CODE_MARKER):
                continue  # dir entries and the runner's completion marker
            if member.issym() or member.islnk():
                raise ArtifactValidationError(f"symlink/hardlink in outputs: {name}")
            if not member.isfile():
                raise ArtifactValidationError(f"not a regular file: {name}")
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ArtifactValidationError(f"path traversal in outputs: {member.name}")
            if len(collected) + 1 > config.max_output_files:
                raise ArtifactValidationError(
                    f"more than {config.max_output_files} output files"
                )
            if member.size > config.max_output_bytes:
                raise ArtifactValidationError(
                    f"{name} is {member.size} bytes > cap {config.max_output_bytes}"
                )
            total += member.size
            if total > config.max_total_output_bytes:
                raise ArtifactValidationError("total output size over cap")
            ext = Path(name).suffix.lower()
            if ext not in config.allowed_extensions:
                raise ArtifactValidationError(
                    f"extension {ext!r} not allowed for {name}; allowed: "
                    f"{list(config.allowed_extensions)}"
                )
            handle = archive.extractfile(member)
            if handle is None:
                raise ArtifactValidationError(f"cannot read output member: {name}")
            data = handle.read()
            try:
                content = data.decode("utf-8")  # strict: reject invalid UTF-8
            except UnicodeDecodeError as exc:
                raise ArtifactValidationError(f"{name} is not valid UTF-8: {exc}") from None
            collected.append(
                CollectedArtifact(
                    path=name,
                    extension=ext,
                    size=member.size,
                    sha256=hashlib.sha256(data).hexdigest(),
                    content=content,
                )
            )
    return collected


async def _run(cmd: list[str], *, timeout: float | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


async def _run_bytes(cmd: list[str], *, timeout: float | None = None) -> tuple[int, bytes, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err.decode("utf-8", errors="replace")


async def _oom_killed(docker: str, container: str) -> bool:
    code, out, _ = await _run(
        [docker, "inspect", "--format", "{{.State.OOMKilled}}", container],
        timeout=15.0,
    )
    return code == 0 and out.strip() == "true"


async def run_sandbox(
    config: SandboxConfig,
    *,
    inputs_dir: Path,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> ExecutionResult:
    """Execute `python -B /inputs/job_script.py` in the restricted
    container. inputs_dir must contain job_script.py (plus whatever
    the job materialized, e.g. snapshot/) and is mounted read-only."""
    inputs_dir = Path(inputs_dir)
    script = inputs_dir / "job_script.py"
    if not script.is_file():
        raise SandboxExecutionError("job_script.py missing from the spool")

    # the container runs as an unprivileged uid: the spool must be
    # world-readable (mkdtemp defaults to 0700)
    os.chmod(inputs_dir, 0o755)
    for p in inputs_dir.rglob("*"):
        if p.is_file():
            os.chmod(p, 0o644)
        elif p.is_dir():
            os.chmod(p, 0o755)

    docker = _docker_binary()
    container = f"youwei-sbx-{uuid.uuid4().hex[:12]}"
    outputs_host = inputs_dir.parent / "outputs"
    outputs_host.mkdir(parents=True, exist_ok=True)

    script_cmd = "python -B /inputs/job_script.py"
    if argv:
        script_cmd += " " + " ".join(shlex.quote(a) for a in argv)
    # trusted wrapper: run the script (stderr merged), record its exit
    # code in the tmpfs, then idle so the tmpfs survives for copy-out
    wrapper = (
        f"{script_cmd} 2>&1; "
        f"echo $? > /outputs/{EXIT_CODE_MARKER}; "
        f"exec sleep {IDLE_SLEEP_SECONDS}"
    )

    cmd = [
        docker, "run", "-d", "--name", container,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", f"{config.sandbox_uid}:{config.sandbox_gid}",
        "--memory", config.memory,
        "--memory-swap", config.memory,
        "--cpus", config.cpus,
        "--pids-limit", str(config.pids_limit),
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,size={config.tmp_tmpfs_size},"
        f"uid={config.sandbox_uid},gid={config.sandbox_gid},mode=0770",
        "--tmpfs",
        f"/outputs:rw,noexec,nosuid,size={config.outputs_tmpfs_size},"
        f"uid={config.sandbox_uid},gid={config.sandbox_gid},mode=0770",
        "-v", f"{inputs_dir.resolve()}:/inputs:ro",
    ]
    if config.runtime:
        cmd += ["--runtime", config.runtime]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [config.image, "sh", "-c", wrapper]

    started = time.monotonic()
    code, out, err = await _run(cmd, timeout=60.0)
    if code != 0:
        raise SandboxExecutionError(f"docker run failed: {err.strip()[:500]}")

    exit_code: int | None = None
    timed_out = False
    oom = False
    try:
        deadline = started + config.timeout_seconds
        while exit_code is None:
            if time.monotonic() >= deadline:
                timed_out = True
                oom = await _oom_killed(docker, container)
                break
            rc, _, _ = await _run(
                [docker, "exec", container, "test", "-f", f"/outputs/{EXIT_CODE_MARKER}"],
                timeout=15.0,
            )
            if rc == 0:
                rc2, marker, _ = await _run(
                    [docker, "exec", container, "cat", f"/outputs/{EXIT_CODE_MARKER}"],
                    timeout=15.0,
                )
                if rc2 == 0:
                    try:
                        exit_code = int(marker.strip())
                    except ValueError:
                        # corrupted marker: treat as infrastructure failure
                        raise SandboxExecutionError(
                            "sandbox exit marker unreadable"
                        ) from None
                    break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # logs while the container is alive (script output; the
        # wrapper adds nothing to stdout)
        _, logs_out, logs_err = await _run(
            [docker, "logs", container], timeout=30.0
        )
        # stream /outputs out of the live tmpfs as a tar (docker cp
        # cannot see tmpfs contents); parsed and validated in memory
        rc, tar_bytes, tar_err = await _run_bytes(
            [docker, "exec", container, "tar", "cf", "-", "-C", "/outputs", "."],
            timeout=60.0,
        )
        if rc != 0:
            raise SandboxExecutionError(
                f"collecting sandbox outputs failed: {tar_err.strip()[:300]}"
            )
    finally:
        try:
            await _run([docker, "rm", "-f", container], timeout=60.0)
        except asyncio.TimeoutError:
            pass

    duration = time.monotonic() - started
    if timed_out:
        if oom:
            raise SandboxExecutionError(
                f"sandbox script was OOM-killed under the memory cap "
                f"{config.memory}"
            )
        raise SandboxExecutionError(
            f"sandbox execution exceeded {config.timeout_seconds}s and was killed"
        )
    if exit_code is None:
        # the container died before writing the marker
        raise SandboxExecutionError(
            "sandbox container died before completion (exit marker missing; "
            "likely OOM or a runner fault)"
        )
    if exit_code == 137:
        raise SandboxExecutionError(
            "sandbox script was killed with SIGKILL (exit 137; likely OOM "
            f"under the memory cap {config.memory})"
        )

    artifacts = _validate_tar(config, tar_bytes)
    return ExecutionResult(
        exit_code=exit_code,
        stdout=_truncate(logs_out, config.log_limit),
        stderr=_truncate(logs_err, config.log_limit),
        artifacts=artifacts,
        duration_seconds=duration,
    )


# --- snapshot spool -----------------------------------------------------------


async def materialize_snapshot(engine, snapshot_id, target: Path) -> dict:
    """Write a frozen snapshot into the spool as content.json +
    manifest.json, verifying hashes on write. The snapshot itself is
    hash-verified on read; the spool re-verification catches
    corruption between read and mount."""
    from youwei_core.data.snapshots import read_snapshot

    target = Path(target)
    snap_dir = target / "snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = await read_snapshot(engine, snapshot_id)  # verifies content hash

    content_path = snap_dir / "content.json"
    content_path.write_text(snap["content"], encoding="utf-8")
    manifest = snap_dir / "manifest.json"
    manifest.write_text(
        json.dumps(snap["manifest"], sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    verify_spool(snap["manifest"]["content_sha256"], snap_dir)
    return {
        "snapshot_id": str(snapshot_id),
        "content_sha256": snap["manifest"]["content_sha256"],
        "files": ["snapshot/content.json", "snapshot/manifest.json"],
    }


def verify_spool(expected_sha: str, snap_dir: Path) -> None:
    """Recompute the content hash from the spooled bytes; raise
    SpoolError on any mismatch."""
    content_path = Path(snap_dir) / "content.json"
    if not content_path.is_file():
        raise SpoolError("spool content.json missing")
    actual = hashlib.sha256(content_path.read_bytes()).hexdigest()
    if actual != expected_sha:
        raise SpoolError(
            f"spool content hash mismatch: expected {expected_sha}, got {actual}"
        )


def cleanup_spool(base: Path) -> None:
    """Remove a spool directory tree (best effort; outputs were
    validated before any host-side reading)."""
    import shutil

    shutil.rmtree(base, ignore_errors=True)
