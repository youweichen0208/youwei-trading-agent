import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "source_check", ROOT / "scripts/check_hermes_source.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def candidate(tmp_path):
    checkout = tmp_path / "upstream"
    checkout.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    file = checkout / "api.py"
    file.write_text("# Source fixture only. Never imported or executed.\n")
    git("add", "api.py")
    git("-c", "user.name=YTA Test", "-c", "user.email=test@invalid", "commit", "-m", "fixture")
    manifest = {
        "schema_version": 1,
        "selection": "candidate-source-only",
        "commit": git("rev-parse", "HEAD"),
        "files": {"api.py": hashlib.sha256(file.read_bytes()).hexdigest()},
    }
    return checkout, manifest


def test_matching_source_is_never_runtime_approval(candidate):
    checkout, manifest = candidate
    result = module.verify(checkout, {**manifest, "integration_status": "PASSED"})
    assert result["status"] == "source_matches"
    assert result["integration_status"] == "UNVERIFIED"


def test_source_checker_rejects_modified_missing_and_untracked_files(candidate):
    checkout, manifest = candidate
    original = (checkout / "api.py").read_bytes()
    (checkout / "api.py").write_text("modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        module.verify(checkout, manifest)
    (checkout / "api.py").unlink()
    with pytest.raises(FileNotFoundError):
        module.verify(checkout, manifest)
    (checkout / "api.py").write_bytes(original)
    (checkout / "extra.py").write_text("untracked")
    with pytest.raises(ValueError, match="untracked"):
        module.verify(checkout, manifest)


def test_source_checker_rejects_wrong_commit_and_path_escape(candidate):
    checkout, manifest = candidate
    with pytest.raises(ValueError, match="locked commit"):
        module.verify(checkout, {**manifest, "commit": "0" * 40})
    for name in ("../outside.py", "/tmp/outside.py"):
        with pytest.raises(ValueError, match="Invalid source entry"):
            module.verify(checkout, {**manifest, "files": {name: "a" * 64}})
    target = checkout.parent / "outside.py"
    target.write_text("outside")
    (checkout / "api.py").unlink()
    (checkout / "api.py").symlink_to(target)
    with pytest.raises(ValueError, match="escapes checkout"):
        module.verify(checkout, manifest)
