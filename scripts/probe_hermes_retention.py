"""Exercise the locked Hermes SQLite store with a controlled clock in a disposable container."""

import hashlib
import json
import os
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


def main():
    if os.environ.get("YTA_HERMES_RETENTION_PROBE") != "1":
        raise RuntimeError("Explicit isolated probe required")
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    if active != {"lo"}:
        raise RuntimeError("Network isolation required")
    from gateway.platforms import api_server_run_idempotency as module

    digest = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    if digest != os.environ["YTA_EXPECTED_STORE_SHA"]:
        raise RuntimeError("Locked store source mismatch")
    Store = module.RunIdempotencyStore
    assert Store.RETENTION_SECONDS == Store.ACKNOWLEDGED_RETENTION_SECONDS == 86400
    results = {}
    with tempfile.TemporaryDirectory(prefix="yta-retention-") as directory:
        path = str(Path(directory) / "runs.db")
        store = Store(path)
        try:
            assert store.durable
            # The clock changes only in this disposable probe process, never a running Gateway.
            with patch.object(module.time, "time", return_value=1000):
                for state in (
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                    "running",
                    "unknown",
                    "execution_unknown",
                ):
                    outcome, _ = store.reserve(
                        "scope", state, "fingerprint", "run_" + state, {"status": state}
                    )
                    assert outcome == "created"
                assert (
                    store.reserve(
                        "other",
                        "completed",
                        "other-fingerprint",
                        "other-run",
                        {"status": "running"},
                    )[0]
                    == "created"
                )
            with patch.object(module.time, "time", return_value=1000 + 86400):
                assert store.lookup("scope", "completed", "fingerprint")[0] == "reused"
            with patch.object(module.time, "time", return_value=1000 + 86401):
                for state in ("completed", "failed", "cancelled", "interrupted"):
                    assert store.lookup("scope", state, "fingerprint")[0] == "missing"
                for state in ("running", "unknown", "execution_unknown"):
                    assert store.lookup("scope", state, "fingerprint")[0] == "reused"
                assert store.lookup("other", "completed", "other-fingerprint")[0] == "reused"
                assert store.lookup("scope", "unknown", "different")[0] == "conflict"
                # This is a reservation only: no prompt or actual model is executed.
                outcome, record = store.reserve(
                    "scope", "completed", "fingerprint", "new-reservation", {"status": "running"}
                )
                assert outcome == "created" and record["run_id"] == "new-reservation"
            results["default_retention_boundary"] = True
            results["all_terminal_states_expire"] = True
            results["active_and_unknown_retained"] = True
            results["scope_isolation"] = True
            results["expired_key_can_create_new_reservation"] = True

            with patch.object(module.time, "time", return_value=100000):
                store.reserve(
                    "scope",
                    "extended",
                    "fp",
                    "run-extended",
                    {"status": "completed"},
                    retention_until=300000,
                )
                store.reserve(
                    "scope",
                    "conflict-extension",
                    "fp",
                    "run-conflict-extension",
                    {"status": "completed"},
                    retention_until=200000,
                )
            with patch.object(module.time, "time", return_value=190000):
                assert (
                    store.lookup("scope", "extended", "fp", retention_until=400000)[0] == "reused"
                )
                assert (
                    store.lookup("scope", "conflict-extension", "wrong", retention_until=500000)[0]
                    == "conflict"
                )
            with patch.object(module.time, "time", return_value=300000):
                assert store.lookup("scope", "extended", "fp")[0] == "reused"
                assert store.lookup("scope", "conflict-extension", "fp")[0] == "missing"
            with patch.object(module.time, "time", return_value=400000):
                assert store.lookup("scope", "extended", "fp")[0] == "missing"
            results["explicit_retention_and_extension"] = True
            results["conflicting_body_cannot_extend_retention"] = True
        finally:
            store.close()

        store = Store(path)
        try:
            with patch.object(module.time, "time", return_value=900000):
                assert store.lookup("scope", "unknown", "fingerprint")[0] == "reused"
                assert store.lookup("scope", "unknown", "changed")[0] == "conflict"
            assert Path(path).stat().st_mode & 0o777 == 0o600
            results["unknown_identity_survives_reopen"] = True
        finally:
            store.close()

        connections = [Store(path) for _ in range(12)]
        try:

            def reserve(index):
                return connections[index].reserve(
                    "parallel",
                    "shared-key",
                    "same-fingerprint",
                    f"proposal-{index}",
                    {"status": "running"},
                )

            with ThreadPoolExecutor(max_workers=12) as pool:
                outcomes = list(pool.map(reserve, range(12)))
            assert sum(outcome == "created" for outcome, _ in outcomes) == 1
            assert len({record["run_id"] for _, record in outcomes}) == 1
            results["twelve_connections_one_reservation"] = True
        finally:
            for connection in connections:
                connection.close()
    print(
        json.dumps(
            {
                "store_sha256": digest,
                "default_retention_seconds": 86400,
                "controlled_clock": True,
                "actual_sqlite": True,
                "network": "none",
                "checks": results,
                "http_expiry_tested": False,
                "model_requests": 0,
                "qualification_status": "UNVERIFIED",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            json.dumps({"error": "RETENTION_PROBE_FAILED", "type": type(error).__name__})
        ) from None
