"""Real-model report formatting from server context; validate independently in BFF."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import probe_hermes_failure as protocol
from probe_hermes_admission_loss import client_for


def admit_or_resume(client, receipt, *, newly_created):
    # A persisted key is not permanent deduplication: upstream retention expires.
    # Only the process that durably created this receipt may send its first POST.
    if receipt.get("run_id"):
        return
    protocol.require(newly_created, "admission_unresolved_no_resubmit")
    status, raw, _ = protocol.request(client, "POST", "/v1/runs", body=receipt["body"])
    protocol.require(status == 202, "admission")
    admitted = json.loads(raw)
    protocol.require(isinstance(admitted.get("run_id"), str) and admitted["run_id"], "run_id")
    receipt["run_id"] = admitted["run_id"]
    protocol.save_receipt(receipt)


def probe(case, revision="original"):
    protocol.require(os.environ.get("YTA_PI_MODEL_PROBE") == "1", "explicit_pi_probe")
    protocol.LIMIT = 2 * 1024 * 1024
    suffix = "" if revision == "original" else "-wording-v2"
    protocol.RECEIPT = Path(f"/opt/data/pi-report-{case}{suffix}.json")
    newly_created = False
    if not protocol.RECEIPT.exists():
        route = json.loads(Path("/opt/data/pi-route.json").read_text())
        context = json.loads(Path("/opt/data/pi-report-input.json").read_text())["cases"][case]
        prompt = (
            Path("/probe/report-sop.md").read_text()
            + "\nFor this conformance test the server has already performed the Alpha calls. "
            "Use the verified context below directly; do not call tools again. "
            "Produce one JSON object in Chinese, no fences. "
            "This is a test; REPLAY inputs are synthetic. "
            "An unavailable LIVE_RESEARCH context contains no market evidence or draft.\n"
            + "OUTPUT JSON SCHEMA:\n"
            + Path("/probe/report-schema.json").read_text()
            + "\nVERIFIED SERVER CONTEXT:\n"
            + json.dumps(context)
        )
        protocol.save_receipt(
            {
                "key": "yta-report-" + secrets.token_hex(16),
                "session_key": "yta-report-session-" + secrets.token_hex(16),
                "body": {"model": route["model"], "input": prompt},
            },
            initial=True,
        )
        newly_created = True
    receipt = json.loads(protocol.RECEIPT.read_text())
    with client_for(receipt) as client:
        admit_or_resume(client, receipt, newly_created=newly_created)
        deadline = time.monotonic() + 150
        while True:
            status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{receipt['run_id']}")
            protocol.require(status == 200, "status_read")
            outcome = json.loads(raw)
            if outcome.get("status") in (
                "completed",
                "failed",
                "cancelled",
                "unknown",
                "execution_unknown",
            ):
                break
            protocol.require(time.monotonic() < deadline, "observation_deadline")
            time.sleep(0.5)
        protocol.require(outcome.get("status") == "completed", "completed")
        report = json.loads(outcome["output"])
        status, raw, _ = protocol.request(client, "GET", f"/v1/runs/{receipt['run_id']}/events")
        protocol.require(status == 200, "events")
        events = [
            json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")
        ]
        protocol.require(
            not any(e.get("event") == "tool.started" for e in events), "no_extra_tools"
        )
        receipt["model_output"] = report
        protocol.save_receipt(receipt)
        return {
            "case": case,
            "requested_model": receipt["body"]["model"],
            "report": report,
            "usage": outcome.get("usage"),
            "status": outcome["status"],
            "extra_tools": 0,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["ready", "unavailable"])
    parser.add_argument("--revision", choices=["original", "wording-v2"], default="original")
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.case, args.revision)))
    except Exception as error:
        stage = str(error) if isinstance(error, protocol.ProbeFailure) else "private_probe_failure"
        print(json.dumps({"error_code": "REPORT_MODEL_PROBE_FAILED", "stage": stage}))
        raise SystemExit(1) from None
