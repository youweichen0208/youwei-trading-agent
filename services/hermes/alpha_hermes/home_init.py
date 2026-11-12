"""Narrow root initialization of the application-owned private home marker.

Upstream initializes home ownership but does not know .alpha-profile.json. A
Linux bind mount retains its preparing host UID, so the unprivileged gateway
cannot read a mode-0600 marker until this validated ownership handoff completes.
"""

import json
import os
import pwd
import stat
import sys
from pathlib import Path

from alpha_hermes.bootstrap import read_json, runtime_config
from alpha_hermes.bridge import BridgeError


def prepare_marker(home: Path, spec: dict, uid: int, gid: int):
    runtime_config(spec)
    if uid <= 0 or gid <= 0:
        raise BridgeError("invalid_profile_runtime_user")
    identity = {key: spec[key] for key in ("version", "ownerId", "profileId", "role")}
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Never follow links or block on a FIFO; inspect the opened inode before
        # reading/mutating it. A renamed path cannot redirect fchown elsewhere.
        marker = os.open(
            ".alpha-profile.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        try:
            info = os.fstat(marker)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
                raise BridgeError("invalid_profile_file")
            with os.fdopen(marker, "rb", closefd=False) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536 or json.loads(raw) != identity:
                raise BridgeError("profile_home_identity_mismatch")
            os.fchown(marker, uid, gid)
            os.fchmod(marker, 0o600)
        finally:
            os.close(marker)
    finally:
        os.close(directory)


def main():
    try:
        if os.getuid() != 0 or os.environ.get("HERMES_HOME") != "/opt/data":
            raise BridgeError("invalid_profile_initialization")
        user = pwd.getpwnam("hermes")
        spec = read_json(Path(os.environ["ALPHA_PROFILE_SPEC"]))
        prepare_marker(Path("/opt/data"), spec, user.pw_uid, user.pw_gid)
    except Exception:
        print(
            "Alpha Profile initialization refused: check its private home identity.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
