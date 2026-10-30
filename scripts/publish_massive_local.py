"""Publish the retained local account probe after deterministic qualification."""

import json
from datetime import datetime
from pathlib import Path

from yta_alpha.calendar import TradingCalendar
from yta_alpha.massive import RawPage
from yta_alpha.models import DataMode
from yta_alpha.qualified_market import QualifiedMarket


def main():
    state = json.loads(Path(".local/massive-live-state.json").read_text())
    root = Path(".local/massive-daily-check")
    result = json.loads((root / "result.json").read_text())
    summaries = tuple(
        RawPage(
            "/v1/open-close/NVDA/" + check["session"],
            (("adjusted", "false"),),
            datetime.fromisoformat(result["observed_at"]),
            (root / (check["session"] + ".json")).read_bytes(),
        )
        for check in result["daily_summary_checks"]
    )
    source = QualifiedMarket(
        "postgresql://yta_app:local-yta-app@127.0.0.1:54329/" + state["database"],
        TradingCalendar(),
        DataMode.LIVE_RESEARCH,
    )
    identity = source.publish(result["acquisition_hash"], summaries)
    print(json.dumps({"qualification_hash": identity, "data_mode": "LIVE_RESEARCH"}))


if __name__ == "__main__":
    main()
