"""Restore a fresh local evidence fixture without discarding intentionally corrupt tests."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import backup_local as backup
from psycopg import sql
from psycopg.types.json import Jsonb
from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.forecast import snapshot_hash
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode
from yta_alpha.store import ResearchStore


def main():
    source = "yta_backup_fixture_" + uuid4().hex + "_test"
    root = Path(__file__).resolve().parents[1]
    with backup.connect("postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source)))
        try:
            migrate(backup.LOCAL + " dbname=" + source, root / "infra/migrations")
            owner = uuid4()
            with backup.connect(source) as connection:
                connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
                connection.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
                connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
                connection.execute(
                    "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
                    (owner, f"{owner}@restore.test"),
                )
            runtime = "host=127.0.0.1 port=54329 user=yta_app password=local-yta-app dbname="
            store = ResearchStore(runtime + source, TradingCalendar(), "a" * 40)
            snapshot = replay_snapshot(store.calendar)
            draft = store.create_draft(owner, snapshot, snapshot.as_of)
            store.freeze(
                owner,
                UUID(draft["draft_id"]),
                "restore-fixture",
                confirmed=True,
                now=snapshot.as_of,
            )
            archive, manifest = backup.backup(source, root / ".local/backups")
            result = backup.restore_drill(archive, manifest)
            assert result["evidence_items_verified"] == 1
            restored = ResearchStore(
                runtime + result["restored_database"], store.calendar, "a" * 40
            )
            assert (
                restored.draft_evidence(owner, UUID(draft["draft_id"]), DataMode.REPLAY).snapshot
                == snapshot
            )
            result["restored_owned_evidence_read"] = True
            # Exercise the new domain verifier against real, inconsistent metadata.
            # Roll this fixture transaction back so the retained restore stays valid.
            damaged = snapshot.model_copy(update={"source_id": f"negative-{uuid4()}"})
            digest = snapshot_hash(damaged)
            try:
                with backup.connect(result["restored_database"]) as connection:
                    connection.execute(
                        "INSERT INTO yta.data_snapshots(hash,data_mode,provider,payload) "
                        "VALUES (%s,'REPLAY',%s,%s)",
                        (digest, damaged.provider, Jsonb(damaged.model_dump(mode="json"))),
                    )
                    connection.execute(
                        "INSERT INTO yta.evidence_items "
                        "(content_hash,source_id,provider,data_mode,as_of,available_at) "
                        "VALUES (%s,'wrong-source',%s,'REPLAY',%s,%s)",
                        (digest, damaged.provider, damaged.as_of, damaged.as_of),
                    )
                    backup.verify_domain(connection)
                    raise AssertionError("Conflicting restored evidence was accepted")
            except RuntimeError as error:
                assert str(error) == "Restored evidence index differs from its snapshot"
            with backup.connect(result["restored_database"]) as connection:
                assert backup.verify_domain(connection)["evidence_items_verified"] == 1
            result["conflicting_index_rejected_and_rolled_back"] = True
            result["scope"] = (
                "One clean synthetic forecast and evidence; not production recovery qualification"
            )
            print(json.dumps(result))
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(source)))


if __name__ == "__main__":
    main()
