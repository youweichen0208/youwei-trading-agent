"""Persistent experiment store tests (S08 D2 minimal requirements 1-2-4-5).

Covers: idempotent registration (same content no-op / different content
conflict), receipt last-write-wins, **restart survival** (receipts and
authorizations reload; a receipt left ``running`` becomes a terminal
``failed (interrupted)`` and stays idempotent), artifact persistence with
hash re-verification, and path traversal rejection.
"""

import hashlib
import json
import uuid

import pytest

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentLimits,
)
from youwei_contracts.sandbox import Artifact, SnapshotBundle
from youwei_runner.experiment_store import (
    ExperimentStore,
    ExperimentStoreError,
    code_sha256,
    snapshot_sha256,
)


def _snapshot(content: str = "security_id,trade_date,close") -> SnapshotBundle:
    return SnapshotBundle(
        snapshot_id=uuid.uuid4(),
        content=content,
        manifest={"content_sha256": hashlib.sha256(content.encode()).hexdigest()},
    )


def _authorization(**overrides) -> ExperimentAuthorization:
    base = dict(
        experiment_invocation_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        attempt_no=1,
        case_id=uuid.uuid4(),
        evidence_sha256="e" * 64,
        exec_config_version="exp-v1",
        limits=ExperimentLimits(
            max_computations=4, max_concurrent=2,
            max_total_duration_seconds=600.0, max_artifact_bytes=1024 * 1024,
        ),
        snapshot=_snapshot(),
    )
    base.update(overrides)
    return ExperimentAuthorization(**base)


def _receipt(exp_id, comp_id, **overrides) -> ExperimentComputationReceipt:
    code = "print(1)"
    base = dict(
        experiment_invocation_id=exp_id,
        computation_id=comp_id,
        request_sha256="a" * 64,
        code_sha256=code_sha256(code),
        image="python:3.13-alpine",
        snapshot_sha256="b" * 64,
        status="succeeded",
        duration_seconds=1.0,
    )
    base.update(overrides)
    return ExperimentComputationReceipt(**base)


def _artifact(path="out.json", content='{"ratio": 1.3}') -> Artifact:
    data = content.encode("utf-8")
    return Artifact(
        path=path, extension=".json", size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), content=content,
    )


# --- registration -----------------------------------------------------------


def test_registration_is_idempotent_for_same_content(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    auth = _authorization()
    store.register(auth)
    store.register(auth)  # same content: no-op
    assert store.authorization(auth.experiment_invocation_id) == auth


def test_registration_conflicts_on_different_content(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    auth = _authorization()
    store.register(auth)
    with pytest.raises(ExperimentStoreError):
        store.register(_authorization(
            experiment_invocation_id=auth.experiment_invocation_id,
            evidence_sha256="f" * 64,
        ))


def test_termination_persists(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    auth = _authorization()
    store.register(auth)
    store.terminate(auth.experiment_invocation_id)
    store.terminate(auth.experiment_invocation_id)  # idempotent

    reloaded = ExperimentStore(tmp_path)
    reloaded.open()
    assert reloaded.is_terminated(auth.experiment_invocation_id)
    assert reloaded.authorization(auth.experiment_invocation_id) == auth


# --- receipts + restart survival (D2 requirement 2) -------------------------


def test_receipts_survive_restart(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp = uuid.uuid4()
    comp = uuid.uuid4()
    store.save_receipt(_receipt(exp, comp, status="succeeded"))

    reloaded = ExperimentStore(tmp_path)
    reloaded.open()
    assert reloaded.receipt(exp, comp).status == "succeeded"
    assert reloaded.computation_count(exp) == 1


def test_running_receipt_becomes_interrupted_on_restart(tmp_path):
    """A receipt left running at restart is terminal (failed/interrupted) and
    stays idempotent: the same id+payload returns the interrupted receipt,
    never a silent re-execution."""
    store = ExperimentStore(tmp_path)
    store.open()
    exp = uuid.uuid4()
    comp = uuid.uuid4()
    store.save_receipt(_receipt(exp, comp, status="running", duration_seconds=0.0))

    reloaded = ExperimentStore(tmp_path)
    reloaded.open()
    receipt = reloaded.receipt(exp, comp)
    assert receipt.status == "failed"
    assert "interrupted" in (receipt.error or "")
    # and the transition is persisted (a second reload sees the same state)
    again = ExperimentStore(tmp_path)
    again.open()
    assert again.receipt(exp, comp).status == "failed"


def test_last_write_wins_for_receipt_transitions(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp = uuid.uuid4()
    comp = uuid.uuid4()
    store.save_receipt(_receipt(exp, comp, status="running", duration_seconds=0.0))
    store.save_receipt(_receipt(exp, comp, status="succeeded", duration_seconds=2.0))

    reloaded = ExperimentStore(tmp_path)
    reloaded.open()
    assert reloaded.receipt(exp, comp).status == "succeeded"
    assert reloaded.used_duration(exp) == 2.0


# --- artifacts ---------------------------------------------------------------


def test_artifact_round_trip_with_hash_verification(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp, comp = uuid.uuid4(), uuid.uuid4()
    art = _artifact()
    store.write_artifacts(exp, comp, [art])
    manifest = ArtifactManifest(
        path=art.path, extension=art.extension, size=art.size, sha256=art.sha256
    )
    back = store.read_artifact(exp, comp, manifest)
    assert back.content == art.content
    assert store.artifact_bytes_used(exp) == len(art.content.encode())


def test_artifact_tamper_detected_on_read(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp, comp = uuid.uuid4(), uuid.uuid4()
    art = _artifact()
    store.write_artifacts(exp, comp, [art])
    # tamper on disk
    target = store.artifact_file(exp, comp, art.path)
    target.write_text('{"ratio": 999}')
    manifest = ArtifactManifest(
        path=art.path, extension=art.extension, size=art.size, sha256=art.sha256
    )
    with pytest.raises(ExperimentStoreError):
        store.read_artifact(exp, comp, manifest)


def test_artifact_path_traversal_rejected(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp, comp = uuid.uuid4(), uuid.uuid4()
    for bad in ("../escape.json", "/abs.json", "a/../b.json", "a\\b.json", ".."):
        with pytest.raises(ExperimentStoreError):
            store.artifact_file(exp, comp, bad)


def test_artifact_collision_with_different_content_rejected(tmp_path):
    store = ExperimentStore(tmp_path)
    store.open()
    exp, comp = uuid.uuid4(), uuid.uuid4()
    store.write_artifacts(exp, comp, [_artifact()])
    with pytest.raises(ExperimentStoreError):
        store.write_artifacts(exp, comp, [_artifact(content='{"ratio": 2.0}')])


# --- helpers ------------------------------------------------------------------


def test_snapshot_and_code_hashing(tmp_path):
    snap = _snapshot("a,b,c")
    assert snapshot_sha256(snap) == hashlib.sha256(b"a,b,c").hexdigest()
    assert code_sha256("print(1)") == hashlib.sha256(b"print(1)").hexdigest()
