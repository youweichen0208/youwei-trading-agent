"""Exact candidate bar projection; never a qualified research snapshot."""

from decimal import Decimal

from yta_alpha.errors import DomainError
from yta_alpha.massive import Acquisition, aggregate_day

COLUMNS = "page_index,row_index,session_date,aggregate_timestamp_ms,open,high,low,close,volume"


def projected_rows(acquisition: Acquisition) -> list[tuple]:
    rows = []
    for page_index, page in enumerate(acquisition.bars):
        payload = page.payload()
        if payload.get("results") and (
            payload.get("adjusted") is not False or payload.get("ticker") != "NVDA"
        ):
            raise ValueError("Raw daily projection requires explicit unadjusted NVDA evidence")
        for row_index, row in enumerate(payload.get("results", [])):
            rows.append(
                (
                    page_index,
                    row_index,
                    aggregate_day(row["t"]),
                    row["t"],
                    *(Decimal(row[key]) for key in ("o", "h", "l", "c", "v")),
                )
            )
    return rows


def persist_bars(connection, digest: str, rows: list[tuple]) -> None:
    """Caller owns the acquisition transaction and advisory lock, including retries."""
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO yta.market_bars(acquisition_hash,{COLUMNS}) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(digest, *row) for row in rows],
        )
    stored = connection.execute(
        f"SELECT {COLUMNS} FROM yta.market_bars WHERE acquisition_hash=%s ORDER BY session_date",
        (digest,),
    ).fetchall()
    if stored != rows:
        raise DomainError(
            "MARKET_BARS_INTEGRITY_FAILED", "Daily records disagree with evidence", status=500
        )
