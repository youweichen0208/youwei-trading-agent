"""Single-process local EOD collector; never runs in a browser request.

Uses the private local account/database files prepared during setup. --watch
checks every 30 minutes. A failed provider read leaves facts untouched and retries
on the next tick; credentials and response bodies are not logged.
"""

import argparse
import asyncio
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr
from yta_alpha.acquire import AcquisitionSettings, collect
from yta_alpha.calendar import TradingCalendar
from yta_alpha.models import DataMode
from yta_alpha.qualified_market import QualifiedMarket

ROOT = Path(__file__).resolve().parents[1]


def refresh():
    calendar = TradingCalendar()
    now = datetime.now(UTC)
    end = calendar.latest_closed(now)
    state = json.loads((ROOT / ".local/massive-live-state.json").read_text())
    url = "postgresql://yta_app:local-yta-app@127.0.0.1:54329/" + state["database"]
    source = QualifiedMarket(url, calendar, DataMode.LIVE_RESEARCH)
    try:
        previous = source.latest(now)
    except Exception:
        previous = None
    if previous is not None and previous.bars[-1].session == end:
        print(json.dumps({"status": "CURRENT", "session": str(end)}), flush=True)
        return
    token = (ROOT / ".local/massive.env").read_text().strip().split("=", 1)[1]
    receipt = asyncio.run(
        collect(
            AcquisitionSettings(massive_token=SecretStr(token), database_url=SecretStr(url)),
            now.date() - timedelta(days=729),
            end,
            DataMode.LIVE_RESEARCH,
        )
    )
    path = ROOT / ".local/massive-history-acquisition.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(receipt, f)
    # The checks resume retained summary responses without refetching completed days.
    for script in ("check_massive_daily_closes.py", "publish_massive_local.py"):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=900,
        )
    print(
        json.dumps(
            {
                "status": "PUBLISHED",
                "session": str(end),
                "acquisition_hash": receipt["acquisition_hash"],
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    with (ROOT / ".local/massive-collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                refresh()
            except Exception as error:
                print(
                    json.dumps({"status": "UNAVAILABLE", "error_type": type(error).__name__}),
                    flush=True,
                )
                if not args.watch:
                    return 1
            if not args.watch:
                return 0
            time.sleep(1800)


if __name__ == "__main__":
    raise SystemExit(main())
