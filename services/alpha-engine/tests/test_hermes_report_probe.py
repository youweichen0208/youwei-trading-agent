import importlib
import json
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def probe(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    module = importlib.import_module("probe_hermes_report")
    monkeypatch.setattr(module.protocol, "RECEIPT", tmp_path / "receipt.json")
    return module


def test_known_run_never_posts_again_even_with_expired_key(probe, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Resuming a known Run must not submit a prompt")

    monkeypatch.setattr(probe.protocol, "request", no_network)
    probe.admit_or_resume(None, {"run_id": "old-run", "key": "expired-key"}, newly_created=False)


def test_unresolved_receipt_requires_reconciliation_without_post(probe, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Unknown admission cannot authorize a second POST")

    monkeypatch.setattr(probe.protocol, "request", no_network)
    with pytest.raises(probe.protocol.ProbeFailure, match="admission_unresolved_no_resubmit"):
        probe.admit_or_resume(None, {"key": "original-key"}, newly_created=False)


def test_new_receipt_submits_once_and_persists_run(probe, monkeypatch):
    receipt = {"key": "original-key", "body": {"input": "test"}}
    probe.protocol.save_receipt(receipt, initial=True)
    calls = []

    def request(client, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 202, b'{"run_id":"accepted-run"}', {}

    monkeypatch.setattr(probe.protocol, "request", request)
    probe.admit_or_resume(None, receipt, newly_created=True)
    recovered = json.loads(probe.protocol.RECEIPT.read_text())
    assert recovered["run_id"] == "accepted-run"
    probe.admit_or_resume(None, recovered, newly_created=False)
    assert calls == [("POST", "/v1/runs", {"body": receipt["body"]})]


@pytest.mark.parametrize("failure", ["timeout", "malformed_response"])
def test_lost_admission_result_cannot_be_resubmitted_on_restart(probe, monkeypatch, failure):
    receipt = {"key": "original-key", "body": {"input": "test"}}
    probe.protocol.save_receipt(receipt, initial=True)
    calls = []

    def request(*args, **kwargs):
        calls.append("POST")
        if failure == "timeout":
            raise httpx.ReadTimeout("response lost after possible acceptance")
        return 202, b'{"unexpected":"body"}', {}

    monkeypatch.setattr(probe.protocol, "request", request)
    with pytest.raises((httpx.ReadTimeout, probe.protocol.ProbeFailure)):
        probe.admit_or_resume(None, receipt, newly_created=True)
    recovered = json.loads(probe.protocol.RECEIPT.read_text())
    assert "run_id" not in recovered
    with pytest.raises(probe.protocol.ProbeFailure, match="admission_unresolved_no_resubmit"):
        probe.admit_or_resume(None, recovered, newly_created=False)
    assert calls == ["POST"]


@pytest.mark.parametrize("scenario", ["missing_run", "execution_unknown", "missing_events"])
def test_resume_failure_never_falls_back_to_admission(probe, monkeypatch, tmp_path, scenario):
    path = tmp_path / "saved.json"
    path.write_text(json.dumps({"run_id": "original-run", "key": "old-key"}))
    monkeypatch.setenv("YTA_PI_MODEL_PROBE", "1")
    monkeypatch.setattr(probe, "Path", lambda _: path)
    monkeypatch.setattr(probe, "client_for", lambda _: nullcontext(None))
    calls = []

    def request(client, method, route, **kwargs):
        calls.append((method, route))
        assert method == "GET"
        if scenario == "missing_run" or route.endswith("/events"):
            return 404, b"{}", {}
        status = "execution_unknown" if scenario == "execution_unknown" else "completed"
        return 200, json.dumps({"status": status, "output": "{}"}).encode(), {}

    monkeypatch.setattr(probe.protocol, "request", request)
    stage = {
        "missing_run": "status_read",
        "execution_unknown": "completed",
        "missing_events": "events",
    }
    with pytest.raises(probe.protocol.ProbeFailure, match=stage[scenario]):
        probe.probe("ready")
    assert calls[0] == ("GET", "/v1/runs/original-run")
    assert json.loads(path.read_text())["run_id"] == "original-run"
