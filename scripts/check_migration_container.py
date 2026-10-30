"""Verify the local migrator in a newly created UUID-named _test DB; never touches app DBs."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.forecast import snapshot_hash
from yta_alpha.migrate import migrate

BASE = "host=127.0.0.1 port=54329 user=yta_owner password=local-yta-owner dbname=postgres"


def run(database: str, role: str | None):
    env = {**os.environ}
    env.pop("YTA_MIGRATION_DATABASE_URL", None)
    if role:
        password = "local-yta-owner" if role == "yta_owner" else "local-yta-app"
        env["YTA_MIGRATION_DATABASE_URL"] = (
            f"postgresql://{role}:{password}@postgres:5432/{database}"
        )
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "yta-dev_default",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp",
            "--env",
            "YTA_MIGRATION_DATABASE_URL",
            "yta-migrator:local",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def main():
    name = "yta_migrate_" + uuid4().hex + "_test"
    with psycopg.connect(BASE, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            for role in (None, "yta_app"):
                result = run(name, role)
                assert result.returncode != 0
            first = run(name, "yta_owner")
            assert first.returncode == 0
            expected = sorted(path.name for path in Path("infra/migrations").glob("*.sql"))
            assert first.stdout.splitlines() == [f"Applied {name}" for name in expected]
            again = run(name, "yta_owner")
            assert again.returncode == 0 and again.stdout == ""
            denied = run(name, "yta_app")
            assert denied.returncode == 1
            assert json.loads(denied.stderr) == {"error_code": "MIGRATION_FAILED"}
            with psycopg.connect(BASE.replace("dbname=postgres", f"dbname={name}")) as connection:
                names = connection.execute(
                    "SELECT name FROM public.yta_migrations ORDER BY name"
                ).fetchall()
                assert [row[0] for row in names] == expected
                assert connection.execute("SELECT count(*) FROM yta.predictions").fetchone()[0] == 0
        finally:
            # Only the random database created by this invocation is eligible for cleanup.
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        upgrade = "yta_upgrade_" + uuid4().hex + "_test"
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(upgrade)))
        try:
            url = BASE.replace("dbname=postgres", f"dbname={upgrade}")
            with TemporaryDirectory(prefix="yta-prior-migrations-") as directory:
                prior = Path(directory)
                for filename in expected[:-1]:
                    shutil.copyfile(Path("infra/migrations") / filename, prior / filename)
                migrate(url, prior)
            with psycopg.connect(url) as connection:
                snapshot = replay_snapshot(TradingCalendar())
                digest = snapshot_hash(snapshot)
                payload = snapshot.model_dump(mode="json")
                connection.execute(
                    "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) "
                    "VALUES (%s,'REPLAY',%s,%s)",
                    (digest, snapshot.provider, Jsonb(payload)),
                )
                connection.execute(
                    "INSERT INTO yta.audit_events(action,resource_id,payload) "
                    "VALUES ('migration.fixture','preserve-existing-evidence','{}'::jsonb)"
                )
            updated = run(upgrade, "yta_owner")
            assert updated.returncode == 0
            assert updated.stdout.splitlines() == [f"Applied {expected[-1]}"]
            with psycopg.connect(url) as connection:
                assert (
                    connection.execute(
                        "SELECT payload FROM yta.data_snapshots WHERE hash=%s", (digest,)
                    ).fetchone()[0]
                    == payload
                )
                assert (
                    connection.execute("SELECT count(*) FROM yta.evidence_items").fetchone()[0] == 0
                )
                assert (
                    connection.execute(
                        "SELECT count(*) FROM yta.audit_events "
                        "WHERE resource_id='preserve-existing-evidence'"
                    ).fetchone()[0]
                    == 1
                )
                assert connection.execute("SELECT count(*) FROM public.yta_migrations").fetchone()[
                    0
                ] == len(expected)
                assert (
                    connection.execute("SELECT count(*) FROM yta.provider_acquisitions").fetchone()[
                        0
                    ]
                    == 0
                )
            repeated = run(upgrade, "yta_owner")
            assert repeated.returncode == 0 and repeated.stdout == ""
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(upgrade)))
    print(
        "Migration container passed: fresh/upgrade, existing evidence, "
        "repeatability, roles, cleanup"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Migration container verification failed (private details not echoed)"
        ) from None
