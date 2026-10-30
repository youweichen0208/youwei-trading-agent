"""Check or explicitly index one existing snapshot; never rewrite original facts."""

import argparse
import json
import os
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from yta_alpha.errors import DomainError
from yta_alpha.evidence import ensure_index, metadata, read_index
from yta_alpha.forecast import snapshot_hash
from yta_alpha.models import DataMode, MarketSnapshot


def backfill(url: str, digest: str, mode: DataMode, *, apply: bool = False) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise DomainError("INVALID_HASH", "Expected a snapshot content hash")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as connection:
        if not apply:
            connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT payload,provider FROM yta.data_snapshots WHERE hash=%s AND data_mode=%s",
            (digest, mode.value),
        ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Snapshot not found", status=404)
        try:
            snapshot = MarketSnapshot.model_validate(row["payload"])
        except ValidationError:
            raise DomainError("EVIDENCE_INVALID", "Stored snapshot is invalid") from None
        expected = metadata(snapshot)
        if (
            snapshot_hash(snapshot) != digest
            or snapshot.data_mode != mode
            or snapshot.provider != row["provider"]
            or expected["available_at"] > snapshot.as_of
        ):
            raise DomainError("EVIDENCE_INVALID", "Stored snapshot failed verification")
        indexed = read_index(connection, digest)
        if indexed is not None and indexed != expected:
            raise DomainError("EVIDENCE_CONFLICT", "Stored evidence index conflicts with snapshot")
        status = "already_indexed" if indexed else "missing"
        if apply and ensure_index(connection, digest, snapshot):
            connection.execute(
                "INSERT INTO yta.audit_events(action,resource_id,payload) "
                "VALUES ('EVIDENCE_INDEX_BACKFILLED',%s,%s)",
                (digest, Jsonb({"data_mode": mode.value, "schema_version": "market-snapshot-v1"})),
            )
            status = "indexed"
        elif apply:
            status = "already_indexed"
    return {"content_hash": digest, "data_mode": mode.value, "status": status, "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("content_hash")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in DataMode])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = backfill(
            os.environ["YTA_DATABASE_URL"], args.content_hash, DataMode(args.mode), apply=args.apply
        )
    except Exception as error:
        code = error.code if isinstance(error, DomainError) else "EVIDENCE_BACKFILL_FAILED"
        raise SystemExit(json.dumps({"error_code": code})) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
