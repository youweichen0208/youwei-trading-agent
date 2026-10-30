"""Read-only review of saved evidence; requires no provider credential or request."""

import argparse
import json
import sys
from uuid import UUID

import psycopg
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from yta_alpha.acquisition_diff import compare
from yta_alpha.acquisition_quality import assess
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.models import DataMode


class InspectionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YTA_ACQUIRE_", env_file=None, extra="ignore", hide_input_in_errors=True
    )
    database_url: SecretStr = Field(min_length=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and summarize saved acquisition evidence")
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--hash")
    identity.add_argument("--attempt-id", type=UUID)
    parser.add_argument("--compare-hash", help="Earlier evidence of the same range and data mode")
    parser.add_argument("--data-mode", choices=[mode.value for mode in DataMode], required=True)
    args = parser.parse_args(argv)
    if args.compare_hash and args.attempt_id:
        parser.error("--compare-hash requires --hash")
    try:
        store = AcquisitionStore(InspectionSettings().database_url.get_secret_value())
        if args.attempt_id:
            result = store.get_attempt(args.attempt_id, data_mode=DataMode(args.data_mode))
        else:
            stored = store.load(args.hash, data_mode=DataMode(args.data_mode))
            acquisition = stored.acquisition
            result = {
                "acquisition_hash": args.hash,
                "data_mode": stored.data_mode.value,
                "requested_start": stored.start.isoformat(),
                "requested_end": stored.end.isoformat(),
                "integrity_checked": True,
                "qualification_status": acquisition.qualification_status,
                "quality": assess(stored, TradingCalendar()),
                "bar_pages": len(acquisition.bars),
                "split_pages": len(acquisition.splits),
                "bar_count": sum(
                    len(page.payload().get("results", [])) for page in acquisition.bars
                ),
                "split_count": len(acquisition.split_events),
                "last_retrieved_at": max(
                    page.retrieved_at for page in (*acquisition.bars, *acquisition.splits)
                ).isoformat(),
            }
            if args.compare_hash:
                before = store.load(args.compare_hash, data_mode=DataMode(args.data_mode))
                result["comparison"] = {
                    "before_hash": args.compare_hash,
                    "after_hash": args.hash,
                    **compare(before, stored),
                }
    except ValueError:
        code = "ACQUISITION_CONFIGURATION_INVALID"
    except DomainError as error:
        code = error.code
    except psycopg.Error:
        code = "ACQUISITION_STORAGE_FAILED"
    except Exception:
        code = "ACQUISITION_INSPECTION_FAILED"
    else:
        print(json.dumps(result))
        return 0
    print(json.dumps({"error_code": code}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
