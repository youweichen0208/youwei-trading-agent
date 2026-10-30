"""Backup boundary checks; the full PostgreSQL restore drill is a separate CI command."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("backup_local", ROOT / "scripts/backup_local.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.mark.parametrize(
    "name",
    [
        "production_remote",
        "yta_backup_fixture_prod_test",
        "yta_backup_fixture_" + "a" * 32,
        "yta_backup_fixture_" + "g" * 32 + "_test",
        "yta_backup_fixture_" + "a" * 32 + "_test_extra",
    ],
)
def test_backup_rejects_arbitrary_database_before_creating_files(tmp_path, name):
    with pytest.raises(ValueError):
        backup.backup(name, tmp_path / "unused")
    assert not (tmp_path / "unused").exists()


def test_private_artifacts_refuse_overwrite(tmp_path):
    path = tmp_path / "private.dump"
    with backup.private_file(path) as output:
        output.write(b"first")
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        backup.private_file(path)
    assert path.read_bytes() == b"first"


def test_corrupt_archive_is_rejected_before_any_database_connection(tmp_path, monkeypatch):
    archive, manifest = tmp_path / "test.dump", tmp_path / "test.json"
    archive.write_bytes(b"damaged archive")
    manifest.write_text(
        json.dumps(
            {
                "format": "yta-local-backup-v1",
                "tables": {"yta.predictions": {}},
                "archive_sha256": "0" * 64,
            }
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("A corrupt archive must never reach database creation")

    monkeypatch.setattr(backup, "connect", forbidden)
    with pytest.raises(RuntimeError, match="checksum"):
        backup.restore_drill(archive, manifest)


@pytest.mark.parametrize(
    "source",
    [
        "production",
        "yta_backup_fixture_prod_test",
        "yta_backup_fixture_" + "a" * 32,
        "yta_backup_fixture_" + "g" * 32 + "_test",
    ],
)
def test_acquisition_restore_rejects_non_fixture_sources(source, monkeypatch):
    import importlib

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = importlib.import_module("check_acquisition_restore")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid source must not reach a database")

    monkeypatch.setattr(module, "connect", forbidden)
    with pytest.raises(ValueError):
        module.check(source, "yta_restore_" + "a" * 32 + "_test")
