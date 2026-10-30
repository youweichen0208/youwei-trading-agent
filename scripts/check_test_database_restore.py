"""Run the real-PG Python suite in a new local DB, then verify its backup restores."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import backup_local as backup
from check_acquisition_restore import check as check_acquisitions
from psycopg import sql
from yta_alpha.migrate import migrate


def main():
    identity = uuid4().hex
    source = f"yta_backup_fixture_{identity}_test"
    log = backup.ROOT / ".local" / f"clean-suite-{identity}.log"
    with backup.connect("postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source)))
        try:
            owner = backup.LOCAL + " dbname=" + source
            migrate(owner, backup.ROOT / "infra/migrations")
            with backup.connect(source) as connection:
                connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
                connection.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
                connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
            runtime = f"postgresql://yta_app:local-yta-app@127.0.0.1:54329/{source}"
            owner_url = f"postgresql://yta_owner:local-yta-owner@127.0.0.1:54329/{source}"
            log.parent.mkdir(parents=True, exist_ok=True)
            with backup.private_file(log) as output:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "--require-postgres"],
                    cwd=backup.ROOT,
                    env={
                        **os.environ,
                        "YTA_TEST_DATABASE_URL": owner_url,
                        "YTA_TEST_RUNTIME_DATABASE_URL": runtime,
                    },
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            if result.returncode:
                raise RuntimeError(f"Python suite failed; inspect {log.name}")
            archive, manifest = backup.backup(source, backup.ROOT / ".local/backups")
            verification = backup.restore_drill(archive, manifest)
            acquisitions = check_acquisitions(source, verification["restored_database"])
            print(
                json.dumps(
                    {
                        "python_suite": "passed",
                        "log": str(log.relative_to(backup.ROOT)),
                        "verification": verification,
                        "acquisitions": acquisitions,
                        "scope": "Fresh local Python test dataset; not remote CI or production",
                    }
                )
            )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(source)))


if __name__ == "__main__":
    main()
