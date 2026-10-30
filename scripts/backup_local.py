"""Private backups and non-destructive restore drills for the isolated local PG17 stack.

Never targets an existing database for restore. Production credentials and remote hosts are
intentionally unsupported; deployment/offsite scheduling remains a separate release gate.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from yta_alpha.calendar import TradingCalendar
from yta_alpha.evidence import metadata
from yta_alpha.forecast import content_hash, forecast, settle
from yta_alpha.models import DataMode, Forecast, MarketSnapshot

ROOT = Path(__file__).resolve().parents[1]
LOCAL = "host=127.0.0.1 port=54329 user=yta_owner password=local-yta-owner"


def connect(name, **options):
    return psycopg.connect(LOCAL, dbname=name, **options)


def pg_command(command, container=None):
    if container:
        return ["docker", "exec", "-i", container, command]
    return [
        "docker",
        "compose",
        "-f",
        str(ROOT / "infra/compose/compose.dev.yaml"),
        "exec",
        "-T",
        "postgres",
        command,
    ]


def private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def run(command, **options):
    result = subprocess.run(command, stderr=subprocess.PIPE, timeout=600, **options)
    if result.returncode or result.stderr.strip():
        # SQL diagnostics can contain backed-up records. Do not print them to the console.
        raise RuntimeError("PostgreSQL backup/restore failed or warned; no verification claimed")


def inventory(connection):
    connection.execute("SET TIME ZONE 'UTC'")
    tables = connection.execute(
        "SELECT schemaname,tablename FROM pg_tables WHERE schemaname='yta' "
        "OR (schemaname='public' AND tablename='yta_migrations') ORDER BY 1,2"
    ).fetchall()
    result = {}
    for schema, table in tables:
        rows = connection.execute(
            sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        ).fetchall()
        digest = hashlib.sha256()
        # Canonical JSON text sorting is independent of insertion order and restore order.
        for value in sorted(r[0] for r in rows):
            digest.update(value.encode())
            digest.update(b"\n")
        result[f"{schema}.{table}"] = {"count": len(rows), "sha256": digest.hexdigest()}
    return result


def verify_domain(connection):
    evidence_count = 0
    proposal_count = 0
    quality_policy_count = 0
    quality_assessment_count = 0
    review_count = 0
    probability_policy_count = 0
    probability_assessment_count = 0
    quality_gate_count = 0
    backtest_count = 0
    training_evidence_count = 0
    verified_gates = {}
    verified_probabilities = {}
    verified_proposals = {}
    verified_assessments = {}
    if connection.execute("SELECT to_regclass('yta.evidence_items')").fetchone()[0]:
        for payload, source, provider, mode, cutoff, available in connection.execute(
            "SELECT s.payload,e.source_id,e.provider,e.data_mode,e.as_of,e.available_at "
            "FROM yta.evidence_items e JOIN yta.data_snapshots s ON s.hash=e.content_hash"
        ):
            expected = metadata(MarketSnapshot.model_validate(payload))
            if list(expected.values()) != [source, provider, mode, cutoff, available]:
                raise RuntimeError("Restored evidence index differs from its snapshot")
            evidence_count += 1
    for table, key, value in [
        ("data_snapshots", "hash", "payload"),
        ("model_versions", "sha256", "config"),
        ("settlement_evidence", "hash", "payload"),
        ("evaluation_reports", "content_hash", "payload"),
    ]:
        for expected, payload in connection.execute(
            sql.SQL("SELECT {},{} FROM yta.{}").format(
                sql.Identifier(key), sql.Identifier(value), sql.Identifier(table)
            )
        ):
            if content_hash(payload) != expected:
                raise RuntimeError(f"Content identity mismatch: {table}")
    from yta_alpha.model_registry import verify_registration

    for identity, digest, payload in connection.execute(
        "SELECT id,sha256,config FROM yta.model_versions WHERE model_kind='TRAINED'"
    ):
        verify_registration(identity, digest, payload)
    calendar = TradingCalendar()
    if connection.execute("SELECT to_regclass('yta.calibration_records')").fetchone()[0]:
        from yta_alpha.calibration_registry import verify_record

        for (
            identity,
            digest,
            payload,
            model_id,
            mode,
            model_digest,
            model_payload,
        ) in connection.execute(
            "SELECT c.id,c.content_hash,c.payload,c.model_version_id,c.data_mode,m.sha256,m.config "
            "FROM yta.calibration_records c JOIN yta.model_versions m ON m.id=c.model_version_id"
        ):
            if payload["model_id"] != model_id or payload["report"]["data_mode"] != mode:
                raise RuntimeError("Restored calibration scope differs")
            model = verify_registration(model_id, model_digest, model_payload).artifact
            verify_record(identity, digest, payload, model, calendar)
    if connection.execute("SELECT to_regclass('yta.model_evaluation_records')").fetchone()[0]:
        from yta_alpha.calibration_registry import verify_record
        from yta_alpha.model_evaluation import evaluate_verified_evidence
        from yta_alpha.model_evaluation_registry import verify_evaluation

        for identity, calibration_id, model_id, digest, mode, payload in connection.execute(
            "SELECT id,calibration_id,model_version_id,content_hash,data_mode,payload "
            "FROM yta.model_evaluation_records"
        ):
            if (
                payload["calibration_id"] != calibration_id
                or payload["model_id"] != model_id
                or payload["cohort"]["data_mode"] != mode
            ):
                raise RuntimeError("Restored model evaluation scope differs")
            calibration_digest, calibration_payload = connection.execute(
                "SELECT content_hash,payload FROM yta.calibration_records WHERE id=%s",
                (calibration_id,),
            ).fetchone()
            model_digest, model_payload = connection.execute(
                "SELECT sha256,config FROM yta.model_versions WHERE id=%s",
                (model_id,),
            ).fetchone()
            model = verify_registration(model_id, model_digest, model_payload).artifact
            verify_record(calibration_id, calibration_digest, calibration_payload, model, calendar)
            replayed = evaluate_verified_evidence(
                calibration_payload, model, calendar, calibration_id
            )
            verify_evaluation(identity, digest, payload, replayed)
    if connection.execute("SELECT to_regclass('yta.model_proposals')").fetchone()[0]:
        from psycopg.rows import dict_row
        from yta_alpha.model_proposals import verify_proposal

        # Referenced evaluation records were fully replayed above on this connection.
        with connection.cursor(row_factory=dict_row) as cursor:
            proposals = cursor.execute("SELECT * FROM yta.model_proposals").fetchall()
        for row in proposals:
            candidate = connection.execute(
                "SELECT payload FROM yta.model_evaluation_records WHERE id=%s",
                (row["candidate_evaluation_id"],),
            ).fetchone()[0]
            reference = None
            if row["reference_evaluation_id"]:
                reference = connection.execute(
                    "SELECT payload FROM yta.model_evaluation_records WHERE id=%s",
                    (row["reference_evaluation_id"],),
                ).fetchone()[0]
            verified_proposals[row["id"]] = verify_proposal(row, candidate, reference)
            proposal_count += 1
    if connection.execute("SELECT to_regclass('yta.model_quality_policies')").fetchone()[0]:
        from psycopg.rows import dict_row
        from yta_alpha.model_quality_registry import verify_assessment, verify_policy

        with connection.cursor(row_factory=dict_row) as cursor:
            policies = {
                row["id"]: verify_policy(row)
                for row in cursor.execute("SELECT * FROM yta.model_quality_policies").fetchall()
            }
            assessments = cursor.execute("SELECT * FROM yta.model_quality_assessments").fetchall()
        quality_policy_count = len(policies)
        for row in assessments:
            candidate = connection.execute(
                "SELECT payload FROM yta.model_evaluation_records WHERE id=%s",
                (row["candidate_evaluation_id"],),
            ).fetchone()[0]
            reference = None
            if row["reference_evaluation_id"]:
                reference = connection.execute(
                    "SELECT payload FROM yta.model_evaluation_records WHERE id=%s",
                    (row["reference_evaluation_id"],),
                ).fetchone()[0]
            # All referenced reports were semantically replayed above.
            verified_assessments[row["id"]] = verify_assessment(
                row, policies[row["policy_id"]], candidate, reference
            )
            quality_assessment_count += 1
    if connection.execute("SELECT to_regclass('yta.probability_quality_policies')").fetchone()[0]:
        from psycopg.rows import dict_row
        from yta_alpha.probability_quality_registry import verify_assessment, verify_policy

        with connection.cursor(row_factory=dict_row) as cursor:
            policies = {
                row["id"]: verify_policy(row)
                for row in cursor.execute(
                    "SELECT * FROM yta.probability_quality_policies"
                ).fetchall()
            }
            assessments = cursor.execute(
                "SELECT * FROM yta.probability_quality_assessments"
            ).fetchall()
        probability_policy_count = len(policies)
        for row in assessments:
            payload, registered_at, digest = connection.execute(
                "SELECT payload,created_at,content_hash FROM yta.calibration_records WHERE id=%s",
                (row["calibration_id"],),
            ).fetchone()
            # Calibration evidence and its model were fully replayed above.
            report = {
                "id": row["calibration_id"],
                "model_id": payload["model_id"],
                "registered_at": registered_at.isoformat(),
                "content_hash": digest,
                "report_hash": content_hash(payload["report"]),
                "report": payload["report"],
            }
            verified_probabilities[row["id"]] = verify_assessment(
                row, policies[row["policy_id"]], report
            )
            probability_assessment_count += 1
    if connection.execute("SELECT to_regclass('yta.model_quality_gates')").fetchone()[0]:
        from psycopg.rows import dict_row
        from yta_alpha.model_quality_gate import ModelQualityGateRequest, combine_quality
        from yta_alpha.model_quality_gate_registry import verify_gate

        with connection.cursor(row_factory=dict_row) as cursor:
            gates = cursor.execute("SELECT * FROM yta.model_quality_gates").fetchall()
        for row in gates:
            proposal = verified_proposals[row["proposal_id"]]
            candidate = connection.execute(
                "SELECT payload FROM yta.model_evaluation_records WHERE id=%s",
                (proposal["proposal"]["candidate_evaluation_id"],),
            ).fetchone()[0]
            request = ModelQualityGateRequest(
                proposal_id=row["proposal_id"],
                price_assessment_id=row["price_assessment_id"],
                probability_assessment_id=row["probability_assessment_id"],
            )
            # Proposals, reports and both assessment types were fully replayed above.
            expected = combine_quality(
                DataMode(row["data_mode"]),
                request,
                proposal,
                candidate,
                verified_assessments[row["price_assessment_id"]],
                verified_probabilities[row["probability_assessment_id"]],
            )
            verified_gates[row["id"]] = verify_gate(row, expected)
            quality_gate_count += 1
    if connection.execute("SELECT to_regclass('yta.model_reviews')").fetchone()[0]:
        from psycopg.rows import dict_row
        from yta_alpha.model_review_registry import verify_review

        with connection.cursor(row_factory=dict_row) as cursor:
            reviews = cursor.execute(
                "SELECT * FROM yta.model_reviews ORDER BY revision,id"
            ).fetchall()
        verified_reviews = {}
        for row in reviews:
            previous = (
                verified_reviews[row["previous_review_id"]] if row["previous_review_id"] else None
            )
            verify_review(
                row,
                verified_proposals[row["proposal_id"]],
                verified_assessments[row["assessment_id"]],
                previous,
                verified_gates.get(row.get("quality_gate_id")),
            )
            verified_reviews[row["id"]] = row
            review_count += 1
    if connection.execute("SELECT to_regclass('yta.training_evidence')").fetchone()[0]:
        from yta_alpha.training_evidence_registry import TrainingEvidenceRegistry

        registry = TrainingEvidenceRegistry(LOCAL + " dbname=" + connection.info.dbname, calendar)
        for identity, mode in connection.execute("SELECT id,data_mode FROM yta.training_evidence"):
            registry.get(identity, mode=DataMode(mode))
            training_evidence_count += 1
    if connection.execute("SELECT to_regclass('yta.backtest_reports')").fetchone()[0]:
        from yta_alpha.backtest_registry import BacktestRegistry

        registry = BacktestRegistry(LOCAL + " dbname=" + connection.info.dbname, calendar)
        for identity, mode in connection.execute("SELECT id,data_mode FROM yta.backtest_reports"):
            registry.get(identity, mode=DataMode(mode))
            backtest_count += 1
    predictions = connection.execute(
        "SELECT p.forecast,s.payload FROM yta.predictions p "
        "JOIN yta.data_snapshots s ON s.hash=p.data_snapshot_hash"
    ).fetchall()
    for saved, snapshot in predictions:
        prediction = Forecast.model_validate(saved)
        replayed = forecast(
            MarketSnapshot.model_validate(snapshot),
            now=prediction.as_of,
            calendar=calendar,
            git_sha=prediction.git_sha,
        )
        if replayed.model_dump(mode="json") != saved:
            raise RuntimeError("Frozen forecast reproduction failed")
    outcomes = connection.execute(
        "SELECT p.forecast,o.result,s.payload,o.observed_at,b.payload "
        "FROM yta.prediction_outcomes o JOIN yta.predictions p ON p.id=o.prediction_id "
        "JOIN yta.data_snapshots s ON s.hash=o.data_snapshot_hash "
        "LEFT JOIN yta.outcome_base_revisions r ON r.outcome_id=o.id "
        "LEFT JOIN yta.data_snapshots b ON b.hash=r.snapshot_hash WHERE o.status='SETTLED'"
    ).fetchall()
    for saved, result, target, observed_at, base in outcomes:
        actual = settle(
            Forecast.model_validate(saved),
            MarketSnapshot.model_validate(target),
            now=observed_at,
            calendar=calendar,
            base_revision=MarketSnapshot.model_validate(base) if base else None,
        )
        computed = actual.model_dump(mode="json")
        # Earlier append-only v1 rows legitimately predate additive provenance fields.
        if any(computed.get(key) != value for key, value in result.items()):
            raise RuntimeError("Settlement reproduction failed")
    return {
        "forecasts_reproduced": len(predictions),
        "settlements_reproduced": len(outcomes),
        "evidence_items_verified": evidence_count,
        "model_proposals_verified": proposal_count,
        "quality_policies_verified": quality_policy_count,
        "quality_assessments_verified": quality_assessment_count,
        "model_reviews_verified": review_count,
        "model_quality_gates_verified": quality_gate_count,
        "backtest_reports_verified": backtest_count,
        "training_evidence_verified": training_evidence_count,
        "probability_policies_verified": probability_policy_count,
        "probability_assessments_verified": probability_assessment_count,
    }


def backup(source, directory, container=None):
    if source not in ("yta", "yta_test") and not re.fullmatch(
        r"yta_backup_fixture_[a-f0-9]{32}_test", source
    ):
        raise ValueError("Only isolated local yta/yta_test databases may be backed up")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    label = f"{source}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    archive = directory / f"{label}.dump"
    manifest = directory / f"{label}.json"
    with connect(source) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        tables = inventory(connection)
        with private_file(archive) as output:
            run(
                pg_command("pg_dump", container)
                + [
                    "-U",
                    "yta_owner",
                    "-d",
                    source,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    f"--snapshot={snapshot}",
                ],
                stdout=output,
            )
        with archive.open("rb") as archive_file:
            archive_hash = hashlib.file_digest(archive_file, "sha256").hexdigest()
        data = {
            "format": "yta-local-backup-v1",
            "source": source,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": tables,
            "archive_sha256": archive_hash,
        }
    with private_file(manifest) as output:
        output.write((json.dumps(data, indent=2) + "\n").encode())
    return archive, manifest


def restore_drill(archive, manifest, container=None):
    expected = json.loads(manifest.read_text())
    if expected.get("format") != "yta-local-backup-v1" or not expected.get("tables"):
        raise ValueError("Unsupported or empty backup manifest")
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected["archive_sha256"]:
            raise RuntimeError("Backup checksum mismatch; restore not started")
    target = f"yta_restore_{uuid4().hex}_test"
    started = time.monotonic()
    with connect("postgres", autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(target))
        )
    # Keep the newly-created database for inspection on both success and failure. Never overwrite.
    with archive.open("rb") as source:
        run(
            pg_command("pg_restore", container)
            + [
                "-U",
                "yta_owner",
                "-d",
                target,
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ],
            stdin=source,
            stdout=subprocess.DEVNULL,
        )
    with connect(target) as connection:
        if inventory(connection) != expected["tables"]:
            raise RuntimeError("Restored row counts or table hashes differ from backup snapshot")
        reproduced = verify_domain(connection)
        identities = connection.execute(
            "SELECT t.relname,a.attname,s.last_value FROM pg_class t "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attidentity<>'' "
            "JOIN pg_class q ON q.oid=pg_get_serial_sequence("
            "format('%I.%I',n.nspname,t.relname),a.attname)::regclass "
            "JOIN pg_sequences s ON s.schemaname=n.nspname AND s.sequencename=q.relname "
            "WHERE n.nspname='yta'"
        ).fetchall()
        for table, column, last_value in identities:
            maximum = connection.execute(
                sql.SQL("SELECT max({}) FROM yta.{}").format(
                    sql.Identifier(column), sql.Identifier(table)
                )
            ).fetchone()[0]
            if maximum is not None and (last_value is None or last_value < maximum):
                raise RuntimeError("Restored identity sequence would reuse an existing ID")
        connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
        connection.execute("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
        connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
        permissions = connection.execute(
            "SELECT bool_and(has_table_privilege('yta_app',c.oid,'SELECT') "
            "AND has_table_privilege('yta_app',c.oid,'INSERT') "
            "AND NOT has_table_privilege('yta_app',c.oid,'UPDATE') "
            "AND NOT has_table_privilege('yta_app',c.oid,'DELETE') "
            "AND NOT has_table_privilege('yta_app',c.oid,'TRUNCATE')) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='yta' AND c.relkind='r'"
        ).fetchone()[0]
        if permissions is not True:
            raise RuntimeError("Restored runtime privileges are not append-only")
        triggers = connection.execute(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='yta' AND t.tgname='immutable_fact' AND t.tgenabled='O'"
        ).fetchall()
        expected_guards = {
            name.removeprefix("yta.")
            for name in expected["tables"]
            if name.startswith("yta.") and name != "yta.users"
        }
        if {table for (table,) in triggers} != expected_guards:
            raise RuntimeError("Immutable guards missing after restore")
        for (table,) in triggers:
            try:
                with connection.transaction():
                    connection.execute(
                        sql.SQL("DELETE FROM yta.{} WHERE false").format(sql.Identifier(table))
                    )
            except psycopg.errors.ObjectNotInPrerequisiteState:
                continue
            raise RuntimeError("Restored immutable trigger did not reject mutation")
    return {
        "restored_database": target,
        "tables_verified": len(expected["tables"]),
        "immutable_guards_verified": len(triggers),
        "identity_sequences_verified": len(identities),
        **reproduced,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["yta", "yta_test"], default="yta_test")
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/backups")
    parser.add_argument(
        "--pg-container", help="CI PostgreSQL container ID; local default uses Compose"
    )
    parser.add_argument("--drill", action="store_true")
    parser.add_argument(
        "--restore-manifest", type=Path, help="Restore a prior trusted local backup"
    )
    args = parser.parse_args()
    if args.restore_manifest:
        manifest = args.restore_manifest
        archive = manifest.with_suffix(".dump")
        result = restore_drill(archive, manifest, args.pg_container)
        print(json.dumps(result, indent=2))
        return
    archive, manifest = backup(args.source, args.directory, args.pg_container)
    result = {"archive": str(archive), "manifest": str(manifest)}
    if args.drill:
        result["verification"] = restore_drill(archive, manifest, args.pg_container)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
