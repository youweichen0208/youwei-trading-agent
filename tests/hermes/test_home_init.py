import json
import os
from pathlib import Path

import pytest
from alpha_hermes.bridge import BridgeError
from alpha_hermes.home_init import prepare_marker
from test_profile_bootstrap import home_for, specification


def ownership_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(
        os, "fchown", lambda fd, uid, gid: calls.append((os.fstat(fd).st_ino, uid, gid))
    )
    return calls


def test_handoff_only_the_matching_marker_without_touching_home_contents(tmp_path, monkeypatch):
    spec = specification()
    home = home_for(tmp_path, spec)
    marker = home / ".alpha-profile.json"
    original = marker.read_bytes()
    other = home / "private.txt"
    other.write_text("unrelated")
    other.chmod(0o400)
    calls = ownership_probe(monkeypatch)
    prepare_marker(home, spec, 10000, 10000)
    assert calls == [(marker.stat().st_ino, 10000, 10000)]
    assert marker.read_bytes() == original
    assert marker.stat().st_mode & 0o777 == 0o600
    assert other.stat().st_mode & 0o777 == 0o400
    assert other.read_text() == "unrelated"


@pytest.mark.parametrize(
    "kind", ["foreign", "symlink", "hardlink", "fifo", "directory", "oversize"]
)
def test_refuse_invalid_marker_before_changing_ownership(tmp_path, monkeypatch, kind):
    spec = specification()
    home = home_for(tmp_path, spec)
    marker = home / ".alpha-profile.json"
    original = marker.read_bytes()
    marker.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(original)
    outside.chmod(0o400)
    if kind == "foreign":
        foreign = specification()
        marker.write_text(
            json.dumps({key: foreign[key] for key in ("version", "ownerId", "profileId", "role")})
        )
    elif kind == "symlink":
        marker.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, marker)
    elif kind == "fifo":
        os.mkfifo(marker)
    elif kind == "directory":
        marker.mkdir()
    else:
        marker.write_bytes(b" " * 65537)
    calls = ownership_probe(monkeypatch)
    with pytest.raises((BridgeError, OSError)):
        prepare_marker(home, spec, 10000, 10000)
    assert not calls
    assert outside.read_bytes() == original
    assert outside.stat().st_mode & 0o777 == 0o400


def test_refuse_symlinked_home_and_root_destination(tmp_path, monkeypatch):
    spec = specification()
    home = home_for(tmp_path, spec)
    linked = tmp_path / "link"
    linked.symlink_to(home, target_is_directory=True)
    calls = ownership_probe(monkeypatch)
    with pytest.raises(OSError):
        prepare_marker(linked, spec, 10000, 10000)
    for uid, gid in ((0, 10000), (10000, 0)):
        with pytest.raises(BridgeError, match="invalid_profile_runtime_user"):
            prepare_marker(Path(home), spec, uid, gid)
    assert not calls
