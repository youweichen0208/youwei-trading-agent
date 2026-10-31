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
from massive_local_common import ROOT, database_url, provider_token
from yta_alpha.acquisition_quality import assess
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.calendar import TradingCalendar
from yta_alpha.massive import aggregate_day, decode_payload
from yta_alpha.models import DataMode


def evidence_path(path: Path) -> Path:
    return path.with_suffix(".metadata.json")


def write_evidence(path: Path, raw: bytes, retrieved_at: datetime):
    metadata = {
        "retrieved_at": retrieved_at.isoformat(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(raw)
    try:
        with os.fdopen(
            os.open(evidence_path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
        ) as f:
            json.dump(metadata, f)
    except Exception:
        path.unlink(missing_ok=True)
        evidence_path(path).unlink(missing_ok=True)
        raise


def read_evidence(path: Path) -> tuple[bytes, datetime]:
    raw = path.read_bytes()
    metadata = json.loads(evidence_path(path).read_text())
    if not isinstance(metadata, dict) or set(metadata) != {"retrieved_at", "sha256"}:
        raise RuntimeError("Daily summary metadata is invalid; reacquire the response")
    if metadata["sha256"] != hashlib.sha256(raw).hexdigest():
        raise RuntimeError("Daily summary evidence integrity failed")
    retrieved_at = datetime.fromisoformat(metadata["retrieved_at"])
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise RuntimeError("Daily summary retrieval time must be timezone-aware")
    return raw, retrieved_at


def main():
    root = ROOT / ".local/massive-daily-check"
    root.mkdir(mode=0o700, exist_ok=True)
    receipt = json.loads((ROOT / ".local/massive-history-acquisition.json").read_text())
    store = AcquisitionStore(database_url())
    stored = store.load(receipt["acquisition_hash"], data_mode=DataMode.LIVE_RESEARCH)
    bars = {
        aggregate_day(row["t"]): row
        for page in stored.acquisition.bars
        for row in page.payload().get("results", [])
    }
    days = sorted(bars)[-21:]
    assert len(days) == 21
    token = provider_token()
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
                cached, _ = read_evidence(path)
                if decode_payload(cached).get("close") != bars[day]["c"]:
                    # Retain the old raw version; a revision requires a new provider check.
                    digest = hashlib.sha256(cached).hexdigest()
                    revision = root / f"{day}-{digest}.revision.json"
                    path.rename(revision)
                    evidence_path(path).rename(evidence_path(revision))
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
                retrieved_at = datetime.now(UTC)
                body = decode_payload(bytes(raw))
                if body.get("status") != "OK" or body.get("symbol") != "NVDA":
                    raise RuntimeError("Unexpected daily summary")
                write_evidence(path, bytes(raw), retrieved_at)
            raw, retrieved_at = read_evidence(path)
            body = decode_payload(raw)
            if body.get("from") != day.isoformat() or body.get("symbol") != "NVDA":
                raise RuntimeError("Summary scope mismatch")
            checks.append(
                {
                    "session": day.isoformat(),
                    "close_equal": body.get("close") == bars[day]["c"],
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "retrieved_at": retrieved_at.isoformat(),
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
