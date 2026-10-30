"""Verify an official source archive against the locked Git tree before extraction."""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def git(checkout: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(checkout), *args],
        stderr=subprocess.PIPE,
        timeout=15,
    )


def blob_digest(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def prepare(checkout: Path, archive: Path, parent: Path, commit: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid locked commit")
    if git(checkout, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("Checkout does not match locked commit")
    expected = {}
    for row in git(checkout, "ls-tree", "-rz", commit).split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        mode, kind, digest = meta.decode().split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("Unsupported source tree entry")
        expected[name.decode()] = (mode, digest)
    prefix = "hermes-agent-" + commit
    destination = parent / prefix
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists")
    normalized = []
    with tarfile.open(archive, "r:gz") as tar:
        files = {}
        for member in tar.getmembers():
            if ".." in PurePosixPath(member.name).parts:
                raise ValueError("Archive path escapes source root")
            if member.name != prefix and not member.name.startswith(prefix + "/"):
                raise ValueError("Unexpected archive prefix")
            if not (member.isfile() or member.isdir()):
                raise ValueError("Unsupported archive entry")
            if member.isfile():
                name = member.name[len(prefix) + 1 :]
                if name in files:
                    raise ValueError("Duplicate archive entry")
                files[name] = member
        if files.keys() != expected.keys():
            raise ValueError("Archive and Git tree differ")
        attributes = tar.extractfile(files[".gitattributes"]).read()
        if blob_digest(attributes) != expected[".gitattributes"][1]:
            raise ValueError("Git attributes mismatch")
        for name, member in files.items():
            data = tar.extractfile(member).read()
            mode, digest = expected[name]
            if blob_digest(data) != digest:
                # GitHub archives apply the locked tree's PowerShell checkout line endings.
                if not (
                    b"*.ps1   text eol=crlf" in attributes
                    and name.endswith(".ps1")
                    and blob_digest(data.replace(b"\r\n", b"\n")) == digest
                ):
                    raise ValueError("Source blob mismatch")
                normalized.append(name)
            if bool(member.mode & 0o111) != (mode == "100755"):
                raise ValueError("Source executable mode mismatch")
        parent.mkdir(parents=True, exist_ok=True)
        tar.extractall(parent, filter="data")
    return {
        "commit": commit,
        "tree": git(checkout, "rev-parse", "HEAD^{tree}").decode().strip(),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "verified_git_blobs": len(expected),
        "ps1_crlf_normalized_for_verification": normalized,
        "build_context": str(destination),
        "integration_status": "UNVERIFIED",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads((ROOT / "infra/hermes/source-lock.json").read_text())
    print(json.dumps(prepare(args.checkout, args.archive, args.output_parent, lock["commit"])))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Hermes archive verification failed; no runtime approval issued") from None
