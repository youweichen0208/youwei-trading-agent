"""Check a read-only Hermes source candidate. Never installs, imports or runs Hermes code."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def verify(checkout: Path, manifest: dict) -> dict:
    if manifest.get("schema_version") != 1 or manifest.get("selection") != "candidate-source-only":
        raise ValueError("Unsupported source lock")
    commit = manifest.get("commit", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid locked commit")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Missing source hashes")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid source entry")
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if head != commit:
        raise ValueError("Checkout does not match locked commit")
    for name, digest in files.items():
        file = checkout / name
        if file.is_symlink() or not file.resolve().is_relative_to(checkout.resolve()):
            raise ValueError("Source entry escapes checkout")
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError("Source hash mismatch")
    dirty = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(checkout),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if dirty:
        raise ValueError("Checkout has local changes or untracked files")
    # This check cannot promote a source checkout into a runtime integration approval.
    return {
        "status": "source_matches",
        "commit": commit,
        "checked_files": len(files),
        "integration_status": "UNVERIFIED",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "infra/hermes/source-lock.json").read_text())
    print(json.dumps(verify(args.checkout, manifest)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Hermes source verification failed; no runtime approval was issued"
        ) from None
