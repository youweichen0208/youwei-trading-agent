"""Exercise compiled BFF readiness on a disposable local _test database, never app data."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from psycopg import sql
from yta_alpha.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "postgresql://yta_owner:local-yta-owner@127.0.0.1:54329/postgres"


def probe(name, role="yta_app"):
    password = "local-yta-app" if role == "yta_app" else "local-yta-owner"
    env = {
        **os.environ,
        "YTA_READY_PROBE_URL": f"postgresql://{role}:{password}@127.0.0.1:54329/{name}",
    }
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import {database} from './dist/database.js';
import {databaseStatus} from './dist/readiness.js';
const pool=database(process.env.YTA_READY_PROBE_URL);
try { console.log(JSON.stringify(await databaseStatus(pool))); }
finally { await pool.end(); }
""",
        ],
        cwd=ROOT / "apps/bff",
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError("Compiled BFF probe failed; build BFF before running")
    return json.loads(result.stdout)


def main():
    name = "yta_ready_" + uuid4().hex + "_test"
    owner_url = ADMIN.rsplit("/", 1)[0] + "/" + name
    observations = {}
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with TemporaryDirectory(prefix="yta-readiness-prior-") as directory:
                for path in (ROOT / "infra/migrations").glob("*.sql"):
                    if path.name < "011_login_account_attempts.sql":
                        shutil.copyfile(path, Path(directory) / path.name)
                migrate(owner_url, Path(directory))

            def change(statement):
                with psycopg.connect(owner_url) as connection:
                    connection.execute(statement)

            def check(label, expected, role="yta_app"):
                observations[label] = probe(name, role)
                assert observations[label] == expected, label

            change("GRANT USAGE ON SCHEMA yta TO yta_app")
            change("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
            change("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
            check("prior_schema", "schema_missing")
            migrate(owner_url, ROOT / "infra/migrations")
            check("new_table_without_grants", "permissions_invalid")
            change("GRANT SELECT, INSERT ON yta.login_account_attempts TO yta_app")
            check("new_sequence_without_grants", "permissions_invalid")
            change("GRANT USAGE, SELECT ON SEQUENCE yta.login_account_attempts_id_seq TO yta_app")
            check("upgraded_and_granted", "ready")
            change("REVOKE USAGE ON SEQUENCE yta.research_messages_id_seq FROM yta_app")
            check("message_sequence_without_usage", "permissions_invalid")
            change("GRANT USAGE ON SEQUENCE yta.research_messages_id_seq TO yta_app")
            change("ALTER TABLE yta.login_account_attempts DISABLE TRIGGER immutable_fact")
            check("account_guard_disabled", "immutable_guards_missing")
            change("ALTER TABLE yta.login_account_attempts ENABLE TRIGGER immutable_fact")
            check("restored", "ready")
            check("owner_is_not_runtime", "permissions_invalid", "yta_owner")
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    print(json.dumps({"checks": observations, "disposable_database_removed": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("BFF readiness fixture failed; no connection details printed") from None
