"""Read-only release input inventory; consistency is not release qualification."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
GATES = {
    "version_and_startup",
    "session_continuity",
    "run_sse_tools",
    "stop_and_recovery",
    "idempotency_and_unknown_execution",
    "profile_isolation",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_migrations(url, catalog):
    """Compare metadata in a read-only transaction; never apply or repair migrations."""
    try:
        with psycopg.connect(
            url,
            connect_timeout=3,
            options=(
                "-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000"
            ),
        ) as connection:
            if connection.execute("SHOW transaction_read_only").fetchone()[0] != "on":
                return {"status": "read_only_not_enforced"}
            rows = connection.execute(
                "SELECT name,sha256 FROM public.yta_migrations ORDER BY name LIMIT 1001"
            ).fetchall()
            if len(rows) > 1000 or any(
                not isinstance(name, str)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9]{3}_[a-z0-9_]+\.sql", name)
                or not HASH.fullmatch(digest)
                for name, digest in rows
            ):
                return {"status": "invalid_metadata"}
            applied = dict(rows)
            if len(applied) != len(rows):
                return {"status": "invalid_metadata"}
        pending = sorted(catalog.keys() - applied.keys())
        unexpected = sorted(applied.keys() - catalog.keys())
        mismatched = sorted(
            name for name in catalog.keys() & applied.keys() if catalog[name] != applied[name]
        )
        status = "conflict" if unexpected or mismatched else "pending" if pending else "consistent"
        return {
            "status": status,
            "read_only": True,
            "applied_count": len(applied),
            "pending": pending,
            "unexpected_applied": unexpected,
            "checksum_conflicts": mismatched,
        }
    except psycopg.errors.UndefinedTable:
        return {"status": "migration_registry_missing"}
    except psycopg.errors.InsufficientPrivilege:
        return {"status": "metadata_permission_missing"}
    except Exception:
        return {"status": "unavailable"}


def inventory(root: Path, inspect_images=False, database_url=None):
    root = root.resolve()
    observation_path = root / "infra/docker/app-local-observation.json"
    lock_path = root / "infra/hermes/source-lock.json"
    observation = json.loads(observation_path.read_text())
    hermes = json.loads(lock_path.read_text())
    checks, images = [], {}
    for name in ("yta-web", "yta-bff", "alpha-engine"):
        images[name] = observation["services"][name]["image_digest"]
    images["migrator"] = observation["migration_probe"]["image_digest"]
    images["hermes"] = hermes["image_digest"]
    for name, digest in images.items():
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("Immutable image digest required")
        if inspect_images:
            # Never pull, build, execute, or select a mutable tag.
            result = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", digest],
                capture_output=True,
                text=True,
                timeout=15,
            )
            checks.append(
                {
                    "item": "image:" + name,
                    "status": "consistent"
                    if result.returncode == 0 and result.stdout.strip() == digest
                    else "missing",
                }
            )

    recorded = observation["files"]
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError("Recorded source evidence required")
    for name, expected in sorted(recorded.items()):
        path = (root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(root):
            raise ValueError("Evidence path outside repository")
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ValueError("Invalid evidence hash")
        status = (
            "missing"
            if not path.is_file()
            else "consistent"
            if sha(path) == expected
            else "changed"
        )
        checks.append({"item": "recorded_file:" + name, "status": status})

    migrations = {p.name: sha(p) for p in sorted((root / "infra/migrations").glob("*.sql"))}
    if not migrations:
        raise ValueError("Migration catalog required")
    latest = sorted(migrations)[-1]
    checks.append(
        {
            "item": "migration_image_latest",
            "status": "consistent"
            if observation["migration_probe"]["latest_migration"] == latest
            else "changed",
        }
    )
    gates = hermes["required_gates"]
    if set(gates) != GATES or any(
        value not in ("UNVERIFIED", "VERIFIED", "FAILED") for value in gates.values()
    ):
        raise ValueError("Complete Hermes gate matrix required")
    database = (
        "NOT_QUERIED" if database_url is None else database_migrations(database_url, migrations)
    )
    if isinstance(database, dict):
        checks.append({"item": "database_migrations", "status": database["status"]})
    consistent = all(check["status"] == "consistent" for check in checks)
    return {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).isoformat(),
        "input_consistency": "consistent" if consistent else "stale_or_missing",
        "release_qualification": "NOT_ASSESSED",
        "images": images,
        "local_image_presence_checked": inspect_images,
        "migration_catalog": migrations,
        "database_migrations": database,
        "hermes": {
            "commit": hermes["commit"],
            "integration_status": hermes["integration_status"],
            "gates": gates,
        },
        "checks": checks,
        "evidence_sha256": {
            "app_observation": sha(observation_path),
            "hermes_lock": sha(lock_path),
        },
        "scope": (
            "Checks recorded source hashes, optional local image IDs and migration registry; "
            "does not attest all build inputs, image contents, target architecture, "
            "or remote deployment."
        ),
        "remaining_release_evidence": [
            "Full locked Hermes integration qualification and real BFF transport",
            "Authorized real market provider and data qualification",
            "Selected baseline/model identity and configuration provenance",
            "Target database migrations, runtime privileges and backup/restore",
            "Staging HTTPS, real end-to-end smoke, monitoring and rollback",
            "Remote CI, review and release acceptance",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-local-images", action="store_true")
    parser.add_argument(
        "--database-env",
        help="Explicit environment variable holding a read-only target connection URL",
    )
    args = parser.parse_args()
    try:
        database_url = None
        if args.database_env:
            database_url = os.environ.get(args.database_env)
            if not database_url:
                raise ValueError("Requested database configuration missing")
        report = inventory(
            Path(__file__).resolve().parents[1], args.inspect_local_images, database_url
        )
    except Exception:
        print(
            json.dumps(
                {"error": "RELEASE_INVENTORY_INVALID", "release_qualification": "NOT_ASSESSED"}
            )
        )
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["input_consistency"] == "consistent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
