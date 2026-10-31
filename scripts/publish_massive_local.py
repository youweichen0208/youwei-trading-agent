"""Publish the retained local account probe after deterministic qualification."""

import json
import stat
from datetime import datetime
from pathlib import Path

from massive_local_common import ROOT, database_url
from yta_alpha.calendar import TradingCalendar
from yta_alpha.massive import RawPage
from yta_alpha.models import DataMode
from yta_alpha.qualified_market import QualificationApproval, QualifiedMarket


def load_summaries(root: Path, result: dict) -> tuple[RawPage, ...]:
    return tuple(
        RawPage(
            "/v1/open-close/NVDA/" + check["session"],
            (("adjusted", "false"),),
            datetime.fromisoformat(check["retrieved_at"]),
            (root / (check["session"] + ".json")).read_bytes(),
        )
        for check in result["daily_summary_checks"]
    )


def load_approval(path: Path) -> QualificationApproval:
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("Massive qualification approval must be a private 0600 file")
    return QualificationApproval.model_validate_json(path.read_text())


def main():
    root = ROOT / ".local/massive-daily-check"
    result = json.loads((root / "result.json").read_text())
    summaries = load_summaries(root, result)
    approval = load_approval(ROOT / ".local/massive-qualification.json")
    source = QualifiedMarket(
        database_url(),
        TradingCalendar(),
        DataMode.LIVE_RESEARCH,
    )
    identity = source.publish(result["acquisition_hash"], summaries, approval)
    print(json.dumps({"qualification_hash": identity, "data_mode": "LIVE_RESEARCH"}))


if __name__ == "__main__":
    main()
