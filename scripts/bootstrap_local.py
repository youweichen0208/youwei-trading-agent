"""Provision only the isolated local Compose database; never accepts a remote URL."""

import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from yta_alpha.migrate import migrate

root = Path(__file__).resolve().parents[1]
base = "host=127.0.0.1 port=54329 user=yta_owner password=local-yta-owner"
with psycopg.connect(base + " dbname=postgres", autocommit=True) as connection:
    for name in ("yta", "yta_test"):
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'yta_app'").fetchone():
        connection.execute("CREATE ROLE yta_app LOGIN PASSWORD 'local-yta-app'")
for name in ("yta", "yta_test"):
    url = base + f" dbname={name}"
    migrate(url, root / "infra/migrations")
    with psycopg.connect(url) as connection:
        connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
        connection.execute("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
        connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
    print(f"Ready: {name} (separate migration owner and append-only application role)")

env_file = root / ".env"
if not env_file.exists():
    example = (root / ".env.example").read_text()
    env_file.write_text(
        example.replace("replace-with-at-least-32-random-characters", secrets.token_urlsafe(48))
    )
    env_file.chmod(0o600)
    print("Created private local .env; generated service token is not printed")
