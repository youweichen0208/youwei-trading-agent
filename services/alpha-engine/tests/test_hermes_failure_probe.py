import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "hermes_failure_probe", ROOT / "scripts/probe_hermes_failure.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_receipt_is_private_exclusive_and_atomically_updated(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(module, "RECEIPT", path)
    original = {"key": "same-key", "body": {"input": "fixture"}}
    module.save_receipt(original, initial=True)
    with pytest.raises(FileExistsError):
        module.save_receipt({"key": "different"}, initial=True)
    assert json.loads(path.read_text()) == original
    updated = {**original, "run_id": "run_fixture"}
    module.save_receipt(updated)
    assert json.loads(path.read_text()) == updated
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_suffix(".pending").exists()


def test_failed_receipt_replace_keeps_original_identity(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(module, "RECEIPT", path)
    original = {"key": "same-key", "body": {"input": "fixture"}}
    module.save_receipt(original, initial=True)

    def fail(*args):
        raise OSError("fixture replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        module.save_receipt({**original, "run_id": "run_fixture"})
    assert json.loads(path.read_text()) == original
    assert path.with_suffix(".pending").stat().st_mode & 0o777 == 0o600
