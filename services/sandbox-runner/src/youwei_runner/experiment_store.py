"""Persistent experiment store (S08, D2 minimal requirements 1-2-4-5).

The Runner's experiment surface must survive restarts: authorizations and
computation receipts are appended to disk (JSONL, last-write-wins per key on
load) and artifact contents live as content-verified files under the store
root. A receipt left ``running`` at load time is rewritten as ``failed``
(interrupted) — the experiment instance retries with a NEW computation id;
the same id + payload keeps returning the interrupted receipt (idempotent),
never a silent re-execution.

Layout::

    <root>/authorizations.jsonl   {"kind":"authorization",...} /
                                  {"kind":"terminated",...}
    <root>/receipts.jsonl         {"kind":"receipt",...} (append per
                                  transition; last line per key wins on load)
    <root>/artifacts/<exp>/<comp>/<path>

Everything here is synchronous, single-event-loop file I/O with flush+fsync
per append (low volume). No database, no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentAuthorization,
    ExperimentComputationReceipt,
)
from youwei_contracts.sandbox import Artifact, SnapshotBundle


class ExperimentStoreError(Exception):
    """Store-level violation: conflicting registration, artifact mismatch,
    or malformed on-disk state."""


_RESTART_ERROR = "runner restarted during computation (interrupted)"


def _fsync_append(path: Path, line: dict) -> None:
    data = json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(data + "\n")
        f.flush()
        os.fsync(f.fileno())


def _safe_join(root: Path, *parts: str) -> Path:
    """Join path parts under root, rejecting traversal/absolute/nonsense."""
    joined = root
    for part in parts:
        p = PurePosixPath(part)
        if (not part or part in (".", "..") or p.is_absolute()
                or ".." in p.parts or "\\" in part or "\0" in part
                or p.as_posix() != part):
            raise ExperimentStoreError(f"invalid store path component {part!r}")
        joined = joined / part
    return joined


class ExperimentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._authorizations: dict[uuid.UUID, ExperimentAuthorization] = {}
        self._terminated: set[uuid.UUID] = set()
        self._receipts: dict[tuple[uuid.UUID, uuid.UUID], ExperimentComputationReceipt] = {}

    # --- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        """Create the layout and (re)load persisted state."""
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        auth_log = self.root / "authorizations.jsonl"
        if auth_log.exists():
            for line in auth_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record["kind"] == "authorization":
                    auth = ExperimentAuthorization.model_validate(record["auth"])
                    self._authorizations[auth.experiment_invocation_id] = auth
                elif record["kind"] == "terminated":
                    self._terminated.add(uuid.UUID(record["experiment_id"]))
        receipts_log = self.root / "receipts.jsonl"
        if receipts_log.exists():
            for line in receipts_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record["kind"] != "receipt":
                    continue
                receipt = ExperimentComputationReceipt.model_validate(record["receipt"])
                self._receipts[
                    (receipt.experiment_invocation_id, receipt.computation_id)
                ] = receipt
        # A receipt still "running" after a restart is an interruption: make
        # it terminal and PERSIST that transition (idempotent replays of the
        # same id+payload keep returning the interrupted receipt).
        for key, receipt in list(self._receipts.items()):
            if receipt.status == "running":
                receipt = receipt.model_copy(
                    update={"status": "failed", "error": _RESTART_ERROR, "partial": False}
                )
                self._receipts[key] = receipt
                self._append_receipt(receipt)

    # --- authorizations ----------------------------------------------------

    def register(self, auth: ExperimentAuthorization) -> None:
        """Idempotent registration: same id + same content is a no-op; the
        same id with different content is a conflict."""
        existing = self._authorizations.get(auth.experiment_invocation_id)
        if existing is not None:
            if existing != auth:
                raise ExperimentStoreError(
                    "experiment already registered with different content"
                )
            return
        self._authorizations[auth.experiment_invocation_id] = auth
        _fsync_append(
            self.root / "authorizations.jsonl",
            {"kind": "authorization", "auth": auth.model_dump(mode="json")},
        )

    def authorization(self, experiment_id: uuid.UUID) -> ExperimentAuthorization | None:
        return self._authorizations.get(experiment_id)

    def terminate(self, experiment_id: uuid.UUID) -> None:
        """Record termination: new computations for this experiment are
        rejected from now on (D2 minimal requirement 3)."""
        if experiment_id in self._terminated:
            return
        self._terminated.add(experiment_id)
        _fsync_append(
            self.root / "authorizations.jsonl",
            {"kind": "terminated", "experiment_id": str(experiment_id)},
        )

    def is_terminated(self, experiment_id: uuid.UUID) -> bool:
        return experiment_id in self._terminated

    # --- receipts ----------------------------------------------------------

    def receipt(
        self, experiment_id: uuid.UUID, computation_id: uuid.UUID
    ) -> ExperimentComputationReceipt | None:
        return self._receipts.get((experiment_id, computation_id))

    def receipts_for(self, experiment_id: uuid.UUID) -> list[ExperimentComputationReceipt]:
        return sorted(
            (r for (e, _), r in self._receipts.items() if e == experiment_id),
            key=lambda r: str(r.computation_id),
        )

    def computation_count(self, experiment_id: uuid.UUID) -> int:
        return sum(1 for (e, _) in self._receipts if e == experiment_id)

    def running_count(self, experiment_id: uuid.UUID) -> int:
        return sum(
            1
            for (e, _), r in self._receipts.items()
            if e == experiment_id and r.status == "running"
        )

    def used_duration(self, experiment_id: uuid.UUID) -> float:
        return sum(
            r.duration_seconds
            for (e, _), r in self._receipts.items()
            if e == experiment_id and r.status != "running"
        )

    def save_receipt(self, receipt: ExperimentComputationReceipt) -> None:
        self._receipts[
            (receipt.experiment_invocation_id, receipt.computation_id)
        ] = receipt
        self._append_receipt(receipt)

    def _append_receipt(self, receipt: ExperimentComputationReceipt) -> None:
        _fsync_append(
            self.root / "receipts.jsonl",
            {"kind": "receipt", "receipt": receipt.model_dump(mode="json")},
        )

    # --- artifacts ---------------------------------------------------------

    def write_artifacts(
        self,
        experiment_id: uuid.UUID,
        computation_id: uuid.UUID,
        artifacts: list[Artifact],
    ) -> None:
        """Persist produced artifacts, verifying size+hash BEFORE writing."""
        for artifact in artifacts:
            target = self.artifact_file(experiment_id, computation_id, artifact.path)
            if target.exists():
                # Content-addressed: the same verified content may be
                # rewritten only if identical; a path collision with
                # different content is a store violation.
                if target.read_text(encoding="utf-8") != artifact.content:
                    raise ExperimentStoreError("artifact path collision with different content")
                continue
            manifest = ArtifactManifest(
                path=artifact.path, extension=artifact.extension,
                size=artifact.size, sha256=artifact.sha256,
            )
            del manifest  # Artifact validation already guarantees the shape
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(artifact.content, encoding="utf-8")
            os.replace(tmp, target)

    def artifact_bytes_used(self, experiment_id: uuid.UUID) -> int:
        base = self.root / "artifacts" / str(experiment_id)
        if not base.exists():
            return 0
        return sum(f.stat().st_size for f in base.rglob("*") if f.is_file())

    def artifact_file(
        self, experiment_id: uuid.UUID, computation_id: uuid.UUID, path: str
    ) -> Path:
        return _safe_join(
            self.root / "artifacts", str(experiment_id), str(computation_id), path
        )

    def read_artifact(
        self,
        experiment_id: uuid.UUID,
        computation_id: uuid.UUID,
        manifest: ArtifactManifest,
    ) -> Artifact:
        """Read one artifact back, re-verifying size and content hash."""
        target = self.artifact_file(experiment_id, computation_id, manifest.path)
        if not target.is_file():
            raise ExperimentStoreError("artifact file missing")
        content = target.read_text(encoding="utf-8")
        data = content.encode("utf-8")
        if len(data) != manifest.size or hashlib.sha256(data).hexdigest() != manifest.sha256:
            raise ExperimentStoreError("artifact content hash mismatch on read")
        return Artifact(
            path=manifest.path, extension=manifest.extension,
            size=manifest.size, sha256=manifest.sha256, content=content,
        )


def snapshot_sha256(snapshot: SnapshotBundle) -> str:
    return hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()


def code_sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def request_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def valid_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value or ""))
