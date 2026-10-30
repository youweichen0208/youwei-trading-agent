"""Local synthetic recovery drill: PG facts, baseline config hashes, regenerated Parquet.

Never restores over an existing database, changes service routing, or calls a provider.
"""

import json
import time
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import backup_local as backup
from psycopg import sql
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.market_lake import export, query
from yta_alpha.massive import Acquisition, RawPage
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode
from yta_alpha.store import ResearchStore


def main():
    identity = uuid4().hex
    source = f"yta_backup_fixture_{identity}_test"
    root = backup.ROOT / ".local" / "recovery-drills" / identity
    root.mkdir(parents=True, mode=0o700)
    runtime = "host=127.0.0.1 port=54329 user=yta_app password=local-yta-app dbname="
    durations = {}
    with backup.connect("postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source)))
        try:
            migrate(backup.LOCAL + " dbname=" + source, backup.ROOT / "infra/migrations")
            owner = uuid4()
            with backup.connect(source) as connection:
                connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
                connection.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
                connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
                connection.execute(
                    "INSERT INTO yta.users(id,email) VALUES(%s,%s)",
                    (owner, f"{owner}@restore.test"),
                )
            research = ResearchStore(runtime + source, TradingCalendar(), "a" * 40)
            snapshot = replay_snapshot(research.calendar)
            draft = research.create_draft(owner, snapshot, snapshot.as_of)
            research.freeze(
                owner,
                UUID(draft["draft_id"]),
                "recovery-fixture",
                confirmed=True,
                now=snapshot.as_of,
            )
            start, end = date(2024, 1, 1), date(2025, 1, 2)
            at = datetime.now(UTC)
            bars = b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":['
            for index, timestamp in enumerate((1704171600000, 1735707600000)):
                bars += (b"," if index else b"") + (
                    b'{"t":'
                    + str(timestamp).encode()
                    + b',"o":100.1234567890123456789012345678901234567890,'
                    + b'"h":102,"l":99,"c":101,"v":123.456}'
                )
            bars += b"]}"
            acquisition = Acquisition(
                (RawPage(f"/v2/aggs/ticker/NVDA/range/1/day/{start}/{end}", (), at, bars),),
                (RawPage("/stocks/v1/splits", (), at, b'{"status":"OK","results":[]}'),),
                (),
            )
            store = AcquisitionStore(runtime + source)
            digest = store.save(acquisition, start=start, end=end, data_mode=DataMode.REPLAY)
            original = export(store, root / "original", digest, DataMode.REPLAY)
            with backup.connect(source) as connection:
                model_hashes = connection.execute(
                    "SELECT sha256 FROM yta.model_versions ORDER BY sha256"
                ).fetchall()
            assert model_hashes
            started = time.monotonic()
            archive, manifest = backup.backup(source, backup.ROOT / ".local/backups")
            durations["backup_seconds"] = round(time.monotonic() - started, 3)
            recovery_started = time.monotonic()
            result = backup.restore_drill(archive, manifest)
            durations["postgres_restore_and_validation_seconds"] = result["elapsed_seconds"]
            restored = result["restored_database"]
            with backup.connect(restored) as connection:
                assert (
                    connection.execute(
                        "SELECT sha256 FROM yta.model_versions ORDER BY sha256"
                    ).fetchall()
                    == model_hashes
                )
            restored_research = ResearchStore(runtime + restored, research.calendar, "a" * 40)
            assert (
                restored_research.draft_evidence(
                    owner, UUID(draft["draft_id"]), DataMode.REPLAY
                ).snapshot
                == snapshot
            )
            rebuilt = export(
                AcquisitionStore(runtime + restored), root / "rebuilt", digest, DataMode.REPLAY
            )
            assert rebuilt == original
            for year in (2024, 2025):
                assert query(
                    root / "rebuilt", digest, DataMode.REPLAY, symbol="NVDA", year=year
                ) == query(root / "original", digest, DataMode.REPLAY, symbol="NVDA", year=year)
            durations["restore_and_rebuild_seconds"] = round(time.monotonic() - recovery_started, 3)
            report = {
                "observed_at": datetime.now(UTC).isoformat(),
                "scope": "Local isolated synthetic fixture; no service failover or remote storage",
                "postgres": result,
                "model_config_hashes_verified": len(model_hashes),
                "owned_evidence_read": True,
                "parquet_manifest_and_file_hashes_match": True,
                "parquet_rows": rebuilt.rows,
                "parquet_partitions": len(rebuilt.partitions),
                "timings": durations,
                "service_rto_seconds": None,
                "production_rpo_seconds": None,
                "trained_model_artifacts": "NOT_IMPLEMENTED",
                "archive": str(archive.relative_to(backup.ROOT)),
                "manifest": str(manifest.relative_to(backup.ROOT)),
                "parquet_root": str(root.relative_to(backup.ROOT)),
                "qualification_status": "UNVERIFIED",
            }
            with backup.private_file(root / "observation.json") as output:
                output.write((json.dumps(report, indent=2) + "\n").encode())
            print(json.dumps(report))
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(source)))


if __name__ == "__main__":
    main()
