"""Local worker progress probe. Uses wall time, never the REPLAY financial clock."""

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class WorkerHealth:
    def __init__(self, path: Path, mode: str, provider_ready: bool):
        self.path, self.mode, self.provider_ready = path, mode, provider_ready
        self.run_id = str(uuid4())

    def write(self, state: str):
        body = {
            "schema_version": 1,
            "service": "yta-settlement-worker",
            "run_id": self.run_id,
            "pid": os.getpid(),
            "data_mode": self.mode,
            "state": state,
            "provider_ready": self.provider_ready,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        # Same-directory rename ensures readers see one complete generation. Temp files are 0600.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(body, file)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def probe(path: Path, mode: str, max_age: int, *, now: datetime | None = None) -> dict:
    reason = "probe_failed"
    try:
        if max_age < 1:
            raise ValueError("Invalid age")
        with path.open("rb") as file:
            raw = file.read(8193)
        if len(raw) > 8192:
            raise ValueError("Oversized heartbeat")
        body = json.loads(raw)
        timestamp = datetime.fromisoformat(body["observed_at"])
        if timestamp.tzinfo is None:
            raise ValueError("Naive heartbeat")
        age = ((now or datetime.now(UTC)) - timestamp).total_seconds()
        if body["schema_version"] != 1 or body["service"] != "yta-settlement-worker":
            raise ValueError("Unknown heartbeat")
        if body["data_mode"] != mode:
            reason = "mode_mismatch"
        elif age < 0 or age > max_age:
            reason = "stale_heartbeat"
        elif body["state"] == "paused":
            reason = "dispatch_paused"
        elif body["state"] != "ready":
            reason = "worker_not_ready"
        elif body["provider_ready"] is not True:
            reason = "provider_unavailable"
        else:
            reason = "ready"
    except Exception:
        pass
    return {
        "status": "ready" if reason == "ready" else "not_ready",
        "reason": reason,
        "expected_mode": mode,
    }


def main():
    parser = argparse.ArgumentParser(description="Check settlement worker progress and provider")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--expect-mode", choices=["REPLAY", "LIVE_RESEARCH", "DEMO"], required=True)
    parser.add_argument("--max-age", type=int, default=180)
    args = parser.parse_args()
    if args.max_age < 1:
        parser.error("Maximum age must be positive")
    result = probe(args.file, args.expect_mode, args.max_age)
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "ready" else 1)


if __name__ == "__main__":
    main()
