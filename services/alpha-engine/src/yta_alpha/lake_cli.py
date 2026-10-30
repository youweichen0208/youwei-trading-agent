"""Export or query one explicit acquisition archive; no provider or model requests."""

import argparse
import json
import sys
from pathlib import Path

from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.errors import DomainError
from yta_alpha.inspect_acquisition import InspectionSettings
from yta_alpha.market_lake import export, query
from yta_alpha.models import DataMode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "query"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--hash", required=True)
    parser.add_argument("--data-mode", choices=[mode.value for mode in DataMode], required=True)
    parser.add_argument("--symbol", choices=["NVDA"])
    parser.add_argument("--year", type=int)
    args = parser.parse_args(argv)
    if args.action == "query" and (args.symbol is None or args.year is None):
        parser.error("query requires --symbol and --year")
    if args.action == "export" and (args.symbol is not None or args.year is not None):
        parser.error("export includes every year of the specified acquisition")
    try:
        mode = DataMode(args.data_mode)
        if args.action == "export":
            store = AcquisitionStore(InspectionSettings().database_url.get_secret_value())
            result = export(store, args.root, args.hash, mode).model_dump(mode="json")
        else:
            result = {
                "acquisition_hash": args.hash,
                "data_mode": mode.value,
                "qualification_status": "UNVERIFIED",
                "rows": query(args.root, args.hash, mode, symbol=args.symbol, year=args.year),
            }
    except DomainError as error:
        code = error.code
    except ValueError:
        code = "MARKET_LAKE_ARGUMENT_INVALID"
    except Exception:
        code = "MARKET_LAKE_OPERATION_FAILED"
    else:
        print(json.dumps(result))
        return 0
    print(json.dumps({"error_code": code}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
