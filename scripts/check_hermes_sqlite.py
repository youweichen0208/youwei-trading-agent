"""Run inside the locked Hermes image; smoke-test its actual Python SQLite runtime."""

import _sqlite3
import json
import sqlite3
import sys
import tempfile
from pathlib import Path


def check() -> dict:
    # This candidate belongs to the 3.53 release line, whose WAL-reset fix is
    # documented by SQLite. This is not a general historical-version allowlist.
    if sqlite3.sqlite_version_info < (3, 53, 0):
        raise RuntimeError("Candidate application SQLite is older than the verified release line")
    with tempfile.TemporaryDirectory(prefix="yta-sqlite-") as directory:
        path = Path(directory) / "smoke.db"
        first = sqlite3.connect(path, timeout=0, isolation_level=None)
        second = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            source_id = first.execute("SELECT sqlite_source_id()").fetchone()[0]
            assert first.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            first.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            first.execute("CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')")
            first.execute("INSERT INTO docs VALUES ('hermes')")
            assert (
                first.execute("SELECT count(*) FROM docs WHERE docs MATCH 'erm'").fetchone()[0] == 1
            )
            first.execute("BEGIN IMMEDIATE")
            first.execute("INSERT INTO facts VALUES (1, 'committed')")
            assert second.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
            try:
                second.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
            else:
                raise AssertionError("Concurrent writer unexpectedly obtained the lock")
            first.execute("COMMIT")
            second.execute("BEGIN IMMEDIATE")
            second.execute("INSERT INTO facts VALUES (2, 'rolled back')")
            second.execute("ROLLBACK")
            assert second.execute("SELECT * FROM facts").fetchall() == [(1, "committed")]
            assert first.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        finally:
            second.close()
            first.close()
        reopened = sqlite3.connect(path)
        try:
            assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert reopened.execute("SELECT * FROM facts").fetchall() == [(1, "committed")]
            assert reopened.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            reopened.close()
    return {
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "sqlite_source_id": source_id,
        "module_origin": _sqlite3.__spec__.origin,
        "checks": [
            "wal",
            "reader_isolation",
            "writer_lock",
            "rollback",
            "checkpoint",
            "reopen",
            "integrity",
            "fts5_trigram",
        ],
        "status": "smoke_passed",
        "integration_status": "UNVERIFIED",
    }


if __name__ == "__main__":
    print(json.dumps(check()))
