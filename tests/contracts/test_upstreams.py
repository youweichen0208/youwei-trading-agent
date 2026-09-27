"""Release checks exercise the public validator CLI, without starting services."""

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
VALIDATOR = REPO / "infra" / "validate_upstreams.py"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def command(*args):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def component(image="example.test/runtime@sha256:" + "a" * 64):
    return {
        "enabled": True,
        "source": {"kind": "git", "revision": "b" * 40},
        "integration_status": "integrated",
        "verification": {"status": "passed", "evidence": {"path": "acceptance.md"}},
        "deployment": {
            "image": image,
            "bindings": [{"service": "runtime", "field": "image"}],
        },
    }


def release_files(tmp_path):
    evidence = tmp_path / "acceptance.md"
    evidence.write_text("Synthetic contract-test evidence, not a production approval.\n")
    value = component()
    value["verification"]["evidence"]["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    lock = write_json(tmp_path / "lock.yaml", {"schema_version": 1, "components": {"runtime": value}})
    compose = write_json(tmp_path / "compose.json", {"services": {"runtime": {"image": value["deployment"]["image"]}}})
    manifest = write_json(tmp_path / "manifest.json", {
        "schema_version": 1,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "compose_sha256": hashlib.sha256(compose.read_bytes()).hexdigest(),
        "components": {"runtime": value["deployment"]},
    })
    return lock, compose, manifest


def check_release(tmp_path, files):
    lock, compose, manifest = files
    return command("--mode", "deployment", "--root", tmp_path,
                   "--lock", lock, "--compose", compose, "--manifest", manifest)


def test_deployment_rejects_enabled_component_without_an_immutable_image(tmp_path):
    lock = write_json(tmp_path / "upstreams.lock.yaml", {
        "schema_version": 1,
        "components": {"runtime": component("example.test/runtime:latest")},
    })
    result = command("--mode", "deployment", "--lock", lock)
    assert result.returncode != 0
    assert "digest" in result.stderr.lower()


def test_catalog_rejects_a_moving_branch_as_a_git_revision(tmp_path):
    value = component()
    value["enabled"] = False
    value["source"]["revision"] = "main"
    lock = write_json(tmp_path / "lock.yaml", {"schema_version": 1, "components": {"runtime": value}})
    result = command("--mode", "catalog", "--lock", lock)
    assert result.returncode != 0
    assert "revision" in result.stderr


def test_deployment_accepts_verified_images_bound_to_compose_and_manifest(tmp_path):
    result = check_release(tmp_path, release_files(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "VALID deployment" in result.stdout


@pytest.mark.parametrize("change, expected", [
    ("compose_image", "binding image mismatch"),
    ("manifest_image", "manifest components"),
    ("lock_bytes", "lock_sha256 mismatch"),
    ("compose_bytes", "compose_sha256 mismatch"),
    ("evidence", "evidence sha256 mismatch"),
    ("unverified", "passed integration verification"),
    ("untracked_service", "all Compose service images"),
    ("build", "cannot rebuild"),
])
def test_deployment_rejects_changed_or_unverified_release_inputs(tmp_path, change, expected):
    files = release_files(tmp_path)
    lock, compose, manifest = files
    if change == "compose_image":
        document = json.loads(compose.read_text())
        document["services"]["runtime"]["image"] = "example.test/runtime@sha256:" + "c" * 64
        write_json(compose, document)
    elif change == "manifest_image":
        document = json.loads(manifest.read_text())
        document["components"]["runtime"]["image"] = "example.test/runtime@sha256:" + "c" * 64
        write_json(manifest, document)
    elif change == "lock_bytes":
        lock.write_text(lock.read_text() + "\n")
    elif change == "compose_bytes":
        compose.write_text(compose.read_text() + "\n")
    elif change == "evidence":
        (tmp_path / "acceptance.md").write_text("Changed after acceptance.\n")
    elif change == "unverified":
        document = json.loads(lock.read_text())
        document["components"]["runtime"]["verification"]["status"] = "smoke_only"
        write_json(lock, document)
    elif change == "untracked_service":
        document = json.loads(compose.read_text())
        document["services"]["untracked"] = {"image": "example.test/extra@sha256:" + "d" * 64}
        write_json(compose, document)
    elif change == "build":
        document = json.loads(compose.read_text())
        document["services"]["runtime"]["build"] = "."
        write_json(compose, document)
    result = check_release(tmp_path, files)
    assert result.returncode != 0
    assert expected in result.stderr


def test_repository_catalog_does_not_claim_a_deployable_release():
    catalog = command("--mode", "catalog")
    deployment = command("--mode", "deployment")
    assert catalog.returncode == 0, catalog.stderr
    assert deployment.returncode != 0
    assert "at least one enabled" in deployment.stderr


def test_deployment_checks_the_sandbox_image_in_runner_environment(tmp_path):
    lock, compose, manifest = release_files(tmp_path)
    sandbox = component("example.test/quant@sha256:" + "e" * 64)
    sandbox["verification"] = json.loads(lock.read_text())["components"]["runtime"]["verification"]
    sandbox["deployment"]["bindings"] = [{"service": "runtime", "field": "environment", "name": "YOUWEI_RUNNER_IMAGE"}]
    document = json.loads(lock.read_text())
    document["components"]["quant"] = sandbox
    write_json(lock, document)
    document = json.loads(compose.read_text())
    document["services"]["runtime"]["environment"] = {"YOUWEI_RUNNER_IMAGE": sandbox["deployment"]["image"]}
    write_json(compose, document)
    release = json.loads(manifest.read_text())
    release["components"]["quant"] = sandbox["deployment"]
    release["lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    release["compose_sha256"] = hashlib.sha256(compose.read_bytes()).hexdigest()
    write_json(manifest, release)
    assert check_release(tmp_path, (lock, compose, manifest)).returncode == 0
    document["services"]["runtime"]["environment"]["YOUWEI_RUNNER_IMAGE"] = "python:latest"
    write_json(compose, document)
    rejected = check_release(tmp_path, (lock, compose, manifest))
    assert rejected.returncode != 0
    assert "binding image mismatch" in rejected.stderr
