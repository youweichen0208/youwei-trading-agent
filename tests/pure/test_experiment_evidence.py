"""S08c-1 pure logic: experiment receipt verification and artifact resolution.

The Controller accepts an experiment's result only after verifying the
Runner's computation receipts against it (D2 minimal requirement 4: trusted
execution evidence). These are pure functions over the experiment-v1 wire
types — no database — so they are testable without PostgreSQL.
"""

import uuid

import pytest

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentComputationReceipt,
    ExperimentResult,
    ExperimentResultComputation,
    experiment_artifact_locator,
    parse_experiment_locator,
)
from youwei_core.ledger.experiment_records import (
    ExperimentEvidenceError,
    ExperimentReferenceError,
    ResolvedExperimentArtifact,
    resolve_experiment_reference,
    verify_experiment_evidence,
)


def _manifest(path="result.json", sha="a" * 64, size=10):
    return ArtifactManifest(path=path, extension=".json", size=size, sha256=sha)


def _receipt(
    experiment_id,
    computation_id,
    *,
    status="succeeded",
    code="print(1)",
    code_sha256=None,
    snapshot_sha256="c" * 64,
    image="youwei-sandbox@sha256:deadbeef",
    artifacts=(),
    partial=False,
):
    import hashlib

    return ExperimentComputationReceipt(
        experiment_invocation_id=experiment_id,
        computation_id=computation_id,
        request_sha256="d" * 64,
        code_sha256=code_sha256 or hashlib.sha256(code.encode()).hexdigest(),
        image=image,
        snapshot_sha256=snapshot_sha256,
        status=status,
        partial=partial,
        artifacts=list(artifacts),
        duration_seconds=1.5,
    )


def _result_computation(computation_id, *, code="print(1)", status="succeeded", artifacts=()):
    import hashlib

    return ExperimentResultComputation(
        computation_id=computation_id,
        code=code,
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        status=status,
        artifacts=list(artifacts),
    )


def _result(computations=(), findings="the ratio is stable", warnings=()):
    return ExperimentResult(findings=findings, warnings=list(warnings), computations=list(computations))


EXP = uuid.uuid4()
SNAP_SHA = "c" * 64


class TestVerifyExperimentEvidence:
    def test_matching_receipts_accept_and_return_image(self):
        comp = uuid.uuid4()
        manifest = _manifest()
        result = _result([_result_computation(comp, artifacts=[manifest])])
        receipts = [_receipt(EXP, comp, artifacts=[manifest])]
        image = verify_experiment_evidence(
            experiment_invocation_id=EXP,
            result=result,
            receipts=receipts,
            snapshot_content_sha256=SNAP_SHA,
        )
        assert image == "youwei-sandbox@sha256:deadbeef"

    def test_failed_computation_with_partial_artifacts_matches(self):
        comp = uuid.uuid4()
        manifest = _manifest()
        result = _result([_result_computation(comp, status="failed", artifacts=[manifest])])
        receipts = [_receipt(EXP, comp, status="failed", partial=True, artifacts=[manifest])]
        verify_experiment_evidence(
            experiment_invocation_id=EXP,
            result=result,
            receipts=receipts,
            snapshot_content_sha256=SNAP_SHA,
        )

    def test_findings_only_experiment_with_no_computations_accepts(self):
        verify_experiment_evidence(
            experiment_invocation_id=EXP,
            result=_result(),
            receipts=[],
            snapshot_content_sha256=SNAP_SHA,
        )

    def test_extra_receipts_not_reported_in_result_are_evidence(self):
        """The instance may omit failed computations from its result; the
        receipts still record them (trusted evidence, append-only)."""
        reported = uuid.uuid4()
        omitted = uuid.uuid4()
        result = _result([_result_computation(reported)])
        receipts = [
            _receipt(EXP, reported),
            _receipt(EXP, omitted, status="failed"),
        ]
        verify_experiment_evidence(
            experiment_invocation_id=EXP,
            result=result,
            receipts=receipts,
            snapshot_content_sha256=SNAP_SHA,
        )

    def test_result_computation_without_receipt_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp)])
        with pytest.raises(ExperimentEvidenceError, match="no runner receipt"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=[],
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_code_hash_mismatch_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp, code="print(1)")])
        receipts = [_receipt(EXP, comp, code_sha256="e" * 64)]
        with pytest.raises(ExperimentEvidenceError, match="code hash"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_status_mismatch_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp, status="failed")])
        receipts = [_receipt(EXP, comp, status="succeeded")]
        with pytest.raises(ExperimentEvidenceError, match="status"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_artifact_manifest_mismatch_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp, artifacts=[_manifest(sha="1" * 64)])])
        receipts = [_receipt(EXP, comp, artifacts=[_manifest(sha="2" * 64)])]
        with pytest.raises(ExperimentEvidenceError, match="artifact"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_running_receipt_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp)])
        receipts = [_receipt(EXP, comp, status="running")]
        with pytest.raises(ExperimentEvidenceError, match="still running"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_receipt_for_another_experiment_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp)])
        receipts = [_receipt(uuid.uuid4(), comp)]
        with pytest.raises(ExperimentEvidenceError, match="another experiment"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_snapshot_hash_mismatch_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp)])
        receipts = [_receipt(EXP, comp, snapshot_sha256="f" * 64)]
        with pytest.raises(ExperimentEvidenceError, match="snapshot"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_inconsistent_images_rejected(self):
        comp = uuid.uuid4()
        other = uuid.uuid4()
        result = _result([_result_computation(comp), _result_computation(other)])
        receipts = [
            _receipt(EXP, comp),
            _receipt(EXP, other, image="youwei-sandbox@sha256:other"),
        ]
        with pytest.raises(ExperimentEvidenceError, match="image"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )

    def test_duplicate_receipts_rejected(self):
        comp = uuid.uuid4()
        result = _result([_result_computation(comp)])
        receipts = [_receipt(EXP, comp), _receipt(EXP, comp)]
        with pytest.raises(ExperimentEvidenceError, match="duplicate"):
            verify_experiment_evidence(
                experiment_invocation_id=EXP,
                result=result,
                receipts=receipts,
                snapshot_content_sha256=SNAP_SHA,
            )


class TestResolveExperimentReference:
    def _outcome(self):
        comp = uuid.uuid4()
        manifest = _manifest()
        receipts = [_receipt(EXP, comp, artifacts=[manifest])]
        return comp, manifest, receipts

    def test_resolves_to_verified_manifest(self):
        comp, manifest, receipts = self._outcome()
        locator = experiment_artifact_locator(EXP, comp, manifest.path)
        resolved = resolve_experiment_reference(
            outcome_receipts=receipts, outcome_experiment_id=EXP, locator=locator
        )
        assert isinstance(resolved, ResolvedExperimentArtifact)
        assert resolved.manifest == manifest
        assert resolved.receipt == receipts[0]

    def test_unknown_experiment_id_rejected(self):
        comp, manifest, receipts = self._outcome()
        locator = experiment_artifact_locator(uuid.uuid4(), comp, manifest.path)
        with pytest.raises(ExperimentReferenceError, match="another experiment"):
            resolve_experiment_reference(
                outcome_receipts=receipts, outcome_experiment_id=EXP, locator=locator
            )

    def test_unknown_computation_rejected(self):
        _, manifest, receipts = self._outcome()
        locator = experiment_artifact_locator(EXP, uuid.uuid4(), manifest.path)
        with pytest.raises(ExperimentReferenceError, match="computation"):
            resolve_experiment_reference(
                outcome_receipts=receipts, outcome_experiment_id=EXP, locator=locator
            )

    def test_unknown_artifact_path_rejected(self):
        comp, _, receipts = self._outcome()
        locator = experiment_artifact_locator(EXP, comp, "other.json")
        with pytest.raises(ExperimentReferenceError, match="artifact"):
            resolve_experiment_reference(
                outcome_receipts=receipts, outcome_experiment_id=EXP, locator=locator
            )

    def test_non_experiment_locator_rejected(self):
        _, _, receipts = self._outcome()
        with pytest.raises(ExperimentReferenceError):
            resolve_experiment_reference(
                outcome_receipts=receipts, outcome_experiment_id=EXP,
                locator="snapshot:xyz/rows/0",
            )

    def test_locator_roundtrip_shape(self):
        comp = uuid.uuid4()
        exp_id, comp_id, path = parse_experiment_locator(
            experiment_artifact_locator(EXP, comp, "out/result.json")
        )
        assert (exp_id, comp_id, path) == (EXP, comp, "out/result.json")
