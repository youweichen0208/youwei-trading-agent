import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    module = importlib.import_module("release_inventory")
    for folder in ("infra/docker", "infra/hermes", "infra/migrations"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "infra/migrations/001_initial.sql").write_text("SELECT 1;\n")
    source = tmp_path / "source.ts"
    source.write_text("export const version = 1;\n")
    digest = "sha256:" + "a" * 64
    observation = {
        "services": {
            name: {"image_digest": digest} for name in ("yta-web", "yta-bff", "alpha-engine")
        },
        "migration_probe": {"image_digest": digest, "latest_migration": "001_initial.sql"},
        "files": {"source.ts": module.sha(source)},
    }
    lock = {
        "image_digest": digest,
        "commit": "a" * 40,
        "integration_status": "UNVERIFIED",
        "required_gates": {gate: "UNVERIFIED" for gate in module.GATES},
    }
    (tmp_path / "infra/docker/app-local-observation.json").write_text(json.dumps(observation))
    (tmp_path / "infra/hermes/source-lock.json").write_text(json.dumps(lock))
    return module, tmp_path


def test_consistency_is_not_release_qualification(fixture):
    module, root = fixture
    report = module.inventory(root)
    assert report["input_consistency"] == "consistent"
    assert report["release_qualification"] == "NOT_ASSESSED"
    assert report["database_migrations"] == "NOT_QUERIED"
    assert not report["local_image_presence_checked"]
    assert all(value == "UNVERIFIED" for value in report["hermes"]["gates"].values())


@pytest.mark.parametrize("change", ["edit", "remove", "new_migration"])
def test_detects_stale_evidence_without_rewriting_it(fixture, change):
    module, root = fixture
    evidence = root / "infra/docker/app-local-observation.json"
    original = evidence.read_bytes()
    if change == "edit":
        (root / "source.ts").write_text("changed")
    elif change == "remove":
        (root / "source.ts").unlink()
    else:
        (root / "infra/migrations/002_new.sql").write_text("SELECT 2;")
    assert module.inventory(root)["input_consistency"] == "stale_or_missing"
    assert evidence.read_bytes() == original


@pytest.mark.parametrize("damage", ["tag", "outside", "bad_hash", "empty_files", "missing_gate"])
def test_rejects_unbounded_or_incomplete_metadata(fixture, damage):
    module, root = fixture
    evidence = root / "infra/docker/app-local-observation.json"
    observed = json.loads(evidence.read_text())
    if damage == "tag":
        observed["services"]["yta-web"]["image_digest"] = "yta-web:latest"
    elif damage == "outside":
        observed["files"] = {"../outside": "a" * 64}
    elif damage == "bad_hash":
        observed["files"]["source.ts"] = "invalid"
    elif damage == "empty_files":
        observed["files"] = {}
    else:
        path = root / "infra/hermes/source-lock.json"
        lock = json.loads(path.read_text())
        lock["required_gates"].pop("profile_isolation")
        path.write_text(json.dumps(lock))
    evidence.write_text(json.dumps(observed))
    with pytest.raises(ValueError):
        module.inventory(root)


def test_missing_local_image_never_triggers_pull_or_execution(fixture, monkeypatch):
    module, root = fixture
    commands = []

    def inspect(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 15
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(module.subprocess, "run", inspect)
    result = module.inventory(root, inspect_images=True)
    assert result["input_consistency"] == "stale_or_missing"
    assert len(commands) == 5
    assert all(command[:3] == ["docker", "image", "inspect"] for command in commands)


def test_database_failure_is_sanitized_and_makes_inputs_inconsistent(fixture, monkeypatch):
    module, root = fixture

    def denied(*args, **kwargs):
        assert "default_transaction_read_only=on" in kwargs["options"]
        assert kwargs["connect_timeout"] == 3
        raise RuntimeError("private-password-marker connection details")

    monkeypatch.setattr(module.psycopg, "connect", denied)
    result = module.inventory(root, database_url="private-url-marker")
    assert result["database_migrations"] == {"status": "unavailable"}
    assert result["input_consistency"] == "stale_or_missing"
    assert "private-" not in json.dumps(result)
