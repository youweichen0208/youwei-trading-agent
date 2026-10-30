"""Compare retained facts without rewriting evidence or re-settling predictions."""

from datetime import date
from decimal import Decimal

from yta_alpha.acquisition_store import StoredAcquisition
from yta_alpha.errors import DomainError
from yta_alpha.massive import aggregate_day


def _wire(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        key: value.isoformat()
        if isinstance(value, date)
        else str(value)
        if isinstance(value, Decimal)
        else value
        for key, value in row.items()
    }


def _changes(before: dict, after: dict) -> list[dict]:
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes.append({"key": key, "before": _wire(old), "after": _wire(new)})
    return changes


def _bars(stored: StoredAcquisition) -> dict:
    return {
        aggregate_day(row["t"]).isoformat(): {
            "open": Decimal(row["o"]),
            "high": Decimal(row["h"]),
            "low": Decimal(row["l"]),
            "close": Decimal(row["c"]),
            "volume": Decimal(row["v"]),
            "aggregate_start_ms": row["t"],
        }
        for page in stored.acquisition.bars
        for row in page.payload().get("results", [])
    }


def _actions(stored: StoredAcquisition) -> dict:
    return {
        event.provider_id: {
            "effective_date": event.effective_date,
            "split_from": event.split_from,
            "split_to": event.split_to,
            "adjustment_type": event.adjustment_type,
        }
        for event in stored.acquisition.split_events
    }


def compare(before: StoredAcquisition, after: StoredAcquisition) -> dict:
    """Inputs must have passed AcquisitionStore.load integrity verification."""
    if (before.start, before.end, before.data_mode) != (after.start, after.end, after.data_mode):
        raise DomainError(
            "ACQUISITION_SCOPE_MISMATCH",
            "Compare acquisitions of the same date range and data mode",
        )
    bars = _changes(_bars(before), _bars(after))
    actions = _changes(_actions(before), _actions(after))
    old_pages = (*before.acquisition.bars, *before.acquisition.splits)
    new_pages = (*after.acquisition.bars, *after.acquisition.splits)
    return {
        "version": "acquisition-diff-v1",
        "qualification_status": "UNVERIFIED",
        "status": "DATA_CHANGED" if bars or actions else "NO_DATA_CHANGE",
        "raw_pages_changed": [page.content_hash for page in old_pages]
        != [page.content_hash for page in new_pages],
        "before_retrieved_at": max(page.retrieved_at for page in old_pages).isoformat(),
        "after_retrieved_at": max(page.retrieved_at for page in new_pages).isoformat(),
        "bar_changes": bars,
        "action_changes": actions,
    }
