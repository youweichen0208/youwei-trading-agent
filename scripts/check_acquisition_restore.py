"""Compare source/restored candidate evidence through the actual append-only runtime role."""

import argparse
import json
import re

from backup_local import connect, inventory
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.errors import DomainError
from yta_alpha.models import DataMode


def read(store: AcquisitionStore, digest: str, mode: str):
    try:
        return store.load(digest, data_mode=DataMode(mode))
    except DomainError as error:
        if error.code != "ACQUISITION_INTEGRITY_FAILED":
            raise
        # Tests deliberately retain malformed, immutable candidate evidence. A
        # byte-for-byte restore must preserve its rejection, not silently repair it.
        return None


def read_bars(store: AcquisitionStore, digest: str, mode: str):
    try:
        return store.daily_bars(digest, data_mode=DataMode(mode))
    except DomainError as error:
        if error.code != "MARKET_BARS_INTEGRITY_FAILED":
            raise
        # Missing legacy projections and deliberately conflicting test rows must
        # remain rejected. Never backfill or repair either database in a check.
        return None


def check(source: str, restored: str) -> dict:
    valid_source = source == "yta_test" or re.fullmatch(
        r"yta_backup_fixture_[a-f0-9]{32}_test", source
    )
    if not valid_source or not re.fullmatch(r"yta_restore_[a-f0-9]{32}_test", restored):
        raise ValueError("Only isolated local test/restore databases are supported")
    with connect(source) as original, connect(restored) as recovered:
        before_inventory, after_inventory = inventory(original), inventory(recovered)
        for table in (
            "yta.provider_acquisitions",
            "yta.provider_response_pages",
            "yta.market_bars",
        ):
            if before_inventory[table] != after_inventory[table]:
                raise RuntimeError("Acquisition evidence or daily records changed during restore")
        originals = original.execute(
            "SELECT hash,data_mode FROM yta.provider_acquisitions ORDER BY hash"
        ).fetchall()
        restored_rows = recovered.execute(
            "SELECT hash,data_mode FROM yta.provider_acquisitions ORDER BY hash"
        ).fetchall()
        if originals != restored_rows or not originals:
            raise RuntimeError("Missing or empty acquisition evidence")
    stores = [
        AcquisitionStore(
            f"host=127.0.0.1 port=54329 user=yta_app password=local-yta-app dbname={name}"
        )
        for name in (source, restored)
    ]
    valid = rejected = 0
    valid_projections = rejected_projections = daily_records = 0
    for digest, mode in originals:
        before, after = (read(store, digest, mode) for store in stores)
        if before != after:
            raise RuntimeError("Restored evidence or integrity classification changed")
        if before is None:
            rejected += 1
        else:
            valid += 1
            bars_before, bars_after = (read_bars(store, digest, mode) for store in stores)
            if bars_before != bars_after:
                raise RuntimeError("Restored daily records or integrity classification changed")
            if bars_before is None:
                rejected_projections += 1
            else:
                valid_projections += 1
                daily_records += len(bars_before)
    return {
        "raw_tables_identical": True,
        "market_bars_identical": True,
        "valid_acquisitions_reconstructed": valid,
        "invalid_acquisitions_still_rejected": rejected,
        "valid_projections_reconstructed": valid_projections,
        "missing_or_invalid_projections_still_rejected": rejected_projections,
        "daily_records_reconstructed": daily_records,
        "qualification_status": "UNVERIFIED",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="yta_test")
    parser.add_argument("--restored", required=True)
    args = parser.parse_args()
    try:
        result = check(args.source, args.restored)
    except Exception:
        raise SystemExit(
            "Acquisition restore verification failed (private details not echoed)"
        ) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
