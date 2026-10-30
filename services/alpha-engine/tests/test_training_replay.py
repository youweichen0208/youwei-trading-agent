import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_training import experiment, fit
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.train_cli import TrainingInput
from yta_alpha.training import TrainConfig, save_artifact
from yta_alpha.training_replay import replay_training, save_replay


@pytest.fixture(scope="module")
def replay_case():
    from yta_alpha.calendar import TradingCalendar

    calendar = TradingCalendar()
    bundle = experiment(calendar, 5)
    artifact = fit(bundle, calendar)
    labels, snapshots, observations, split_config = bundle
    spec = TrainingInput(
        labels=labels,
        snapshots=snapshots,
        observations=observations,
        split_config=split_config,
        config=artifact.config,
        fold_index=0,
        cutoff=observations[-1].as_of,
    )
    return artifact, spec, calendar


def test_retraining_matches_every_field_and_saves_private_immutable_report(replay_case, tmp_path):
    artifact, spec, calendar = replay_case
    report = replay_training(artifact, spec, calendar=calendar)
    assert report["status"] == "MATCHED"
    assert report["mismatched_fields"] == []
    assert set(report["matched_fields"]) == set(type(artifact).model_fields)
    assert report["expected_artifact_content_hash"] == report["replayed_artifact_content_hash"]
    assert report["input_hash"] == content_hash(spec.model_dump(mode="json"))
    assert report["content_hash"] == content_hash(
        {k: v for k, v in report.items() if k != "content_hash"}
    )
    assert report["qualification"] == "UNVERIFIED" and not report["promotion_allowed"]
    assert not {"model_text", "snapshots", "observations", "labels"} & report.keys()
    artifact_path = tmp_path / "model.json"
    digest = save_artifact(artifact, artifact_path)
    path = tmp_path / "replay.json"
    saved = save_replay(report, digest, path)
    assert saved == hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600
    payload = json.loads(path.read_text())
    assert payload["artifact_file_sha256"] == digest
    assert payload["content_hash"] == content_hash(
        {k: v for k, v in payload.items() if k != "content_hash"}
    )
    with pytest.raises(FileExistsError):
        save_replay(report, digest, path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == saved
    assert not list(tmp_path.glob(".training-replay-*"))


@pytest.mark.parametrize("change", ["metrics", "source", "config"])
def test_retraining_reports_field_differences_without_approving(replay_case, change):
    artifact, spec, calendar = replay_case
    if change == "metrics":
        artifact = artifact.model_copy(update={"test_mae": artifact.test_mae + Decimal("0.1")})
        expected = "test_mae"
    elif change == "source":
        artifact = artifact.model_copy(
            update={"source_hashes": {**artifact.source_hashes, "training.py": "0" * 64}}
        )
        expected = "source_hashes"
    else:
        spec = spec.model_copy(
            update={"config": TrainConfig(rounds=11, min_data_in_leaf=2, num_leaves=4)}
        )
        expected = "config"
    report = replay_training(artifact, spec, calendar=calendar)
    assert report["status"] == "MISMATCH"
    assert expected in report["mismatched_fields"]
    assert report["expected_artifact_content_hash"] != report["replayed_artifact_content_hash"]
    assert report["qualification"] == "UNVERIFIED" and not report["promotion_allowed"]


def test_retraining_rejects_invalid_original_values_and_missing_evidence(replay_case, monkeypatch):
    artifact, spec, calendar = replay_case
    invalid = spec.model_copy(update={"config": spec.config.model_copy(update={"rounds": True})})
    with pytest.raises(ValidationError):
        replay_training(artifact, invalid, calendar=calendar)
    invalid = spec.model_copy(update={"observations": spec.observations[:-1]})
    with pytest.raises(DomainError) as error:
        replay_training(artifact, invalid, calendar=calendar)
    assert error.value.code == "LABEL_EVIDENCE_MISMATCH"
    from yta_alpha import training

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "Native runtime unavailable", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    with pytest.raises(DomainError) as error:
        replay_training(artifact, spec, calendar=calendar)
    assert error.value.code == "TRAINING_UNAVAILABLE"


def test_training_replay_cli_exit_codes_and_no_overwrite(replay_case, tmp_path):
    artifact, spec, _ = replay_case
    path, inputs, output = (
        tmp_path / "model.json",
        tmp_path / "input.json",
        tmp_path / "report.json",
    )
    digest = save_artifact(artifact, path)
    inputs.write_text(spec.model_dump_json())
    command = [
        sys.executable,
        "-m",
        "yta_alpha.train_cli",
        "replay-training",
        "--input",
        str(inputs),
        "--artifact",
        str(path),
        "--sha256",
        digest,
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "MATCHED"
    original = output.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and json.loads(result.stdout) == {
        "error_code": "EXPERIMENT_FAILED"
    }
    assert output.read_bytes() == original
    changed = deepcopy(spec.model_dump(mode="json"))
    changed["config"]["rounds"] += 1
    inputs.write_text(json.dumps(changed))
    command[-1] = str(tmp_path / "different.json")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "MISMATCH"
    command[-3] = "0" * 64
    command[-1] = str(tmp_path / "bad.json")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and not (tmp_path / "bad.json").exists()


def test_training_replay_cli_exposes_only_unavailable_code(
    replay_case, tmp_path, monkeypatch, capsys
):
    from yta_alpha import training
    from yta_alpha.train_cli import main

    artifact, spec, _ = replay_case
    path, inputs, output = (
        tmp_path / "model.json",
        tmp_path / "input.json",
        tmp_path / "report.json",
    )
    digest = save_artifact(artifact, path)
    inputs.write_text(spec.model_dump_json())

    def unavailable():
        raise DomainError("TRAINING_UNAVAILABLE", "private runtime details", status=503)

    monkeypatch.setattr(training, "library", unavailable)
    assert (
        main(
            [
                "replay-training",
                "--input",
                str(inputs),
                "--artifact",
                str(path),
                "--sha256",
                digest,
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out) == {"error_code": "TRAINING_UNAVAILABLE"}
    assert not output.exists()
