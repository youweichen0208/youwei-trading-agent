import importlib.util
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "archive_check", ROOT / "scripts/prepare_hermes_archive.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def source(tmp_path):
    checkout = tmp_path / "git"
    checkout.mkdir()
    files = {
        ".gitattributes": b"*.ps1   text eol=crlf\n",
        "main.py": b"# Static fixture; never executed.\n",
        "script.ps1": b"# Static fixture.\n",
    }
    for name, data in files.items():
        (checkout / name).write_bytes(data)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], stderr=subprocess.PIPE)

    git("init")
    git("add", ".")
    git("-c", "user.name=YTA Test", "-c", "user.email=test@invalid", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD").decode().strip()
    files["script.ps1"] = files["script.ps1"].replace(b"\n", b"\r\n")
    return checkout, commit, files


def archive(path, commit, files, *, extra=None, executable=False):
    prefix = "hermes-agent-" + commit + "/"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mode = 0o755 if executable and name == "main.py" else 0o644
            tar.addfile(info, io.BytesIO(data))
        if extra:
            tar.addfile(extra)


def test_verifies_entire_tree_and_documented_line_endings_before_extracting(source, tmp_path):
    checkout, commit, files = source
    file = tmp_path / "source.tgz"
    archive(file, commit, files)
    parent = tmp_path / "out"
    result = module.prepare(checkout, file, parent, commit)
    assert result["verified_git_blobs"] == 3
    assert result["ps1_crlf_normalized_for_verification"] == ["script.ps1"]
    assert result["integration_status"] == "UNVERIFIED"
    assert (Path(result["build_context"]) / "main.py").read_bytes() == files["main.py"]
    with pytest.raises(ValueError, match="already exists"):
        module.prepare(checkout, file, parent, commit)


@pytest.mark.parametrize("change", ["modified", "missing", "extra", "mode", "link", "escape"])
def test_rejects_changed_content_or_unsafe_entries_before_extraction(source, tmp_path, change):
    checkout, commit, files = source
    extra = None
    if change == "modified":
        files["main.py"] = b"changed"
    elif change == "missing":
        del files["main.py"]
    elif change == "extra":
        files["unexpected.py"] = b"extra"
    elif change in ("link", "escape"):
        extra = tarfile.TarInfo("hermes-agent-" + commit + "/../outside")
        extra.type = tarfile.DIRTYPE
        if change == "link":
            extra.name = "hermes-agent-" + commit + "/link"
            extra.type = tarfile.SYMTYPE
            extra.linkname = "../outside"
    file = tmp_path / "source.tgz"
    archive(file, commit, files, extra=extra, executable=change == "mode")
    parent = tmp_path / "out"
    with pytest.raises(ValueError):
        module.prepare(checkout, file, parent, commit)
    assert not parent.exists()
    assert not (tmp_path / "outside").exists()


def test_wrong_commit_fails_before_archive_access(source, tmp_path):
    checkout, _commit, _files = source
    with pytest.raises(ValueError, match="locked commit"):
        module.prepare(checkout, tmp_path / "missing.tgz", tmp_path / "out", "0" * 40)
