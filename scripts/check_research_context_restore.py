"""Restore the quiescent pi application dataset and replay its immutable tool evidence."""

import json
from uuid import uuid4

import backup_local as backup
from psycopg import sql
from yta_alpha.calendar import TradingCalendar
from yta_alpha.models import DataMode
from yta_alpha.research_tools import ResearchTools
from yta_alpha.store import ResearchStore


def main():
    state = json.loads((backup.ROOT / ".local/hermes-application-state.json").read_text())
    original = state["database"]
    if not original.startswith("yta_pi_app_") or not original.endswith("_test"):
        raise ValueError("Isolated pi test database required")
    clone = "yta_backup_fixture_" + uuid4().hex + "_test"
    with backup.connect("postgres", autocommit=True) as admin:
        # PostgreSQL rejects an active source; never terminate another worker to copy it.
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(clone), sql.Identifier(original)
            )
        )
        try:
            archive, manifest = backup.backup(clone, backup.ROOT / ".local/backups")
            restored = backup.restore_drill(archive, manifest)
            target = restored["restored_database"]
            calendar = TradingCalendar()
            tools = ResearchTools(
                ResearchStore(
                    f"postgresql://yta_app:local-yta-app@127.0.0.1:54329/{target}",
                    calendar,
                    "local-dev",
                )
            )
            with backup.connect(target) as c:
                contexts = c.execute(
                    "SELECT owner_id,run_id FROM yta.research_tool_contexts"
                ).fetchall()
                before = c.execute("SELECT count(*) FROM yta.audit_events").fetchone()[0]
            count = 0
            for owner, run in contexts:
                result = tools.get(owner, run, DataMode.REPLAY)
                assert {row["tool"] for row in result["receipts"]} == {
                    "market_snapshot",
                    "indicators",
                }
                count += len(result["receipts"])
            with backup.connect(target) as c:
                assert c.execute("SELECT count(*) FROM yta.audit_events").fetchone()[0] == before
            assert len(contexts) == 1 and count == 2
            print(
                json.dumps(
                    {
                        "verification": restored,
                        "contexts_replayed": len(contexts),
                        "tool_receipts_replayed": count,
                        "read_only_audit_unchanged": True,
                        "original_database": original,
                    }
                )
            )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(clone)))


if __name__ == "__main__":
    main()
