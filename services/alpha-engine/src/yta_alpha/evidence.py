"""Content index metadata; no supplier qualification or financial recalculation."""

from yta_alpha.errors import DomainError
from yta_alpha.models import MarketSnapshot


def metadata(snapshot: MarketSnapshot) -> dict:
    return {
        "source_id": snapshot.source_id,
        "provider": snapshot.provider,
        "data_mode": snapshot.data_mode.value,
        "as_of": snapshot.as_of,
        "available_at": max(
            [bar.available_at for bar in snapshot.bars]
            + [split.available_at for split in snapshot.splits]
        ),
    }


def read_index(connection, digest: str) -> dict | None:
    return connection.execute(
        "SELECT source_id,provider,data_mode,as_of,available_at "
        "FROM yta.evidence_items WHERE content_hash=%s",
        (digest,),
    ).fetchone()


def ensure_index(connection, digest: str, snapshot: MarketSnapshot) -> bool:
    expected = metadata(snapshot)
    if expected["available_at"] > expected["as_of"]:
        raise DomainError("EVIDENCE_INVALID", "Evidence is newer than its information cutoff")
    inserted = connection.execute(
        "INSERT INTO yta.evidence_items "
        "(content_hash,source_id,provider,data_mode,as_of,available_at) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING content_hash",
        (digest, *expected.values()),
    ).fetchone()
    if read_index(connection, digest) != expected:
        raise DomainError("EVIDENCE_CONFLICT", "Stored evidence index conflicts with snapshot")
    return inserted is not None
