"""Checksum-verified migrations; separate owner credentials from runtime credentials."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg


def migrate(database_url: str, migrations: Path) -> list[str]:
    files = sorted(migrations.glob("*.sql"))
    if not migrations.is_dir() or not files:
        raise RuntimeError("Migration catalog is missing or empty")
    catalog = {file.name: file.read_text() for file in files}
    if any(not source.strip() for source in catalog.values()):
        raise RuntimeError("Migration catalog contains an empty script")
    hashes = {name: hashlib.sha256(source.encode()).hexdigest() for name, source in catalog.items()}
    applied = []
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(891123411)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS public.yta_migrations "
            "(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz DEFAULT now())"
        )
        existing = dict(connection.execute("SELECT name,sha256 FROM public.yta_migrations"))
        for name in catalog.keys() & existing.keys():
            if existing[name] != hashes[name]:
                raise RuntimeError(f"Applied migration checksum changed: {name}")
        if existing.keys() - catalog.keys():
            raise RuntimeError(
                "Migration catalog omits applied migrations; refusing an older artifact"
            )
        for name, source in catalog.items():
            if name in existing:
                continue
            digest = hashes[name]
            connection.execute(source)
            connection.execute(
                "INSERT INTO public.yta_migrations(name, sha256) VALUES (%s, %s)",
                (name, digest),
            )
            applied.append(name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("infra/migrations"))
    args = parser.parse_args()
    url = os.environ.get("YTA_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("YTA_MIGRATION_DATABASE_URL is required (value not printed)")
    try:
        applied = migrate(url, args.directory)
    except Exception:
        print(json.dumps({"error_code": "MIGRATION_FAILED"}), file=sys.stderr)
        raise SystemExit(1) from None
    for name in applied:
        print(f"Applied {name}")


if __name__ == "__main__":
    main()
