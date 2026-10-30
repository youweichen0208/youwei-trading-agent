"""Explicit low-rate comparison of stored raw bars with Massive daily summaries.

This writes private verification artifacts, never publishes a qualified snapshot.
Completed daily reads are reused on resume; no model or financial fact is created.
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from yta_alpha.acquisition_quality import assess
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.calendar import TradingCalendar
from yta_alpha.massive import aggregate_day, decode_payload
from yta_alpha.models import DataMode


def main():
    root = Path(".local/massive-daily-check")
    root.mkdir(mode=0o700, exist_ok=True)
    state = json.loads(Path(".local/massive-live-state.json").read_text())
    receipt = json.loads(Path(".local/massive-history-acquisition.json").read_text())
    store = AcquisitionStore(
        "postgresql://yta_app:local-yta-app@127.0.0.1:54329/" + state["database"]
    )
    stored = store.load(receipt["acquisition_hash"], data_mode=DataMode.LIVE_RESEARCH)
    bars = {
        aggregate_day(row["t"]): row
        for page in stored.acquisition.bars
        for row in page.payload().get("results", [])
    }
    days = sorted(bars)[-21:]
    assert len(days) == 21
    token = Path(".local/massive.env").read_text().strip().split("=", 1)[1]
    checks = []
    with httpx.Client(
        base_url="https://api.massive.com",
        headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for day in days:
            path = root / f"{day}.json"
            if path.exists():
                cached = decode_payload(path.read_bytes())
                if cached.get("close") != bars[day]["c"]:
                    # Retain the old raw version; a revision requires a new provider check.
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    path.rename(root / f"{day}-{digest}.revision.json")
            if not path.exists():
                # Also wait before the first request to separate preceding operator probes.
                time.sleep(15.1)
                with client.stream(
                    "GET", f"/v1/open-close/NVDA/{day}", params={"adjusted": "false"}
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"Daily summary unavailable: HTTP {response.status_code}"
                        )
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise RuntimeError("Oversized provider response")
                body = decode_payload(bytes(raw))
                if body.get("status") != "OK" or body.get("symbol") != "NVDA":
                    raise RuntimeError("Unexpected daily summary")
                with os.fdopen(
                    os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as f:
                    f.write(raw)
            raw = path.read_bytes()
            body = decode_payload(raw)
            if body.get("from") != day.isoformat() or body.get("symbol") != "NVDA":
                raise RuntimeError("Summary scope mismatch")
            checks.append(
                {
                    "session": day.isoformat(),
                    "close_equal": body.get("close") == bars[day]["c"],
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    result = {
        "observed_at": datetime.now(UTC).isoformat(),
        "acquisition_hash": receipt["acquisition_hash"],
        "calendar_diagnostics": assess(stored, TradingCalendar()),
        "daily_summary_checks": checks,
        "all_closes_equal": all(check["close_equal"] for check in checks),
        "qualification_status": "UNVERIFIED",
        "scope": "Actual latest 21 daily closes compared; not full provider qualification",
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not serialize provider bodies, credentials, connection URLs or arbitrary errors.
        raise SystemExit(
            json.dumps({"error": "DAILY_CHECK_FAILED", "type": type(error).__name__})
        ) from None
