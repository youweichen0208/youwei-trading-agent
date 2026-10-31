import importlib.util
import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daily = load_script("check_massive_daily_closes")
publish = load_script("publish_massive_local")


def test_response_evidence_preserves_distinct_retrieval_times(tmp_path):
    first = datetime(2026, 10, 29, 20, 1, tzinfo=UTC)
    second = first + timedelta(seconds=20)
    first_path = tmp_path / "2026-10-28.json"
    second_path = tmp_path / "2026-10-29.json"
    daily.write_evidence(first_path, b'{"close":100}', first)
    daily.write_evidence(second_path, b'{"close":101}', second)

    assert daily.read_evidence(first_path) == (b'{"close":100}', first)
    assert daily.read_evidence(second_path) == (b'{"close":101}', second)
    assert stat.S_IMODE(daily.evidence_path(first_path).stat().st_mode) == 0o600


def test_cached_response_requires_original_time_metadata(tmp_path):
    path = tmp_path / "2026-10-29.json"
    path.write_bytes(b"{}")

    with pytest.raises(FileNotFoundError):
        daily.read_evidence(path)


def test_cached_response_rejects_hash_mismatch_and_naive_time(tmp_path):
    path = tmp_path / "2026-10-29.json"
    daily.write_evidence(path, b'{"close":100}', datetime.now(UTC))
    path.write_bytes(b'{"close":101}')
    with pytest.raises(RuntimeError, match="integrity"):
        daily.read_evidence(path)

    path = tmp_path / "naive.json"
    daily.write_evidence(path, b'{"close":100}', datetime.now(UTC))
    metadata_path = daily.evidence_path(path)
    metadata = json.loads(metadata_path.read_text())
    metadata["retrieved_at"] = "2026-10-29T20:00:00"
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(RuntimeError, match="timezone-aware"):
        daily.read_evidence(path)


def test_publication_uses_each_response_retrieval_time(tmp_path):
    first = datetime(2026, 10, 29, 20, 1, tzinfo=UTC)
    second = first + timedelta(seconds=20)
    for session in ("2026-10-28", "2026-10-29"):
        (tmp_path / f"{session}.json").write_bytes(b"{}")
    result = {
        "daily_summary_checks": [
            {"session": "2026-10-28", "retrieved_at": first.isoformat()},
            {"session": "2026-10-29", "retrieved_at": second.isoformat()},
        ]
    }

    summaries = publish.load_summaries(tmp_path, result)

    assert [summary.retrieved_at for summary in summaries] == [first, second]


def test_publication_requires_private_valid_qualification_approval(tmp_path):
    path = tmp_path / "approval.json"
    payload = {
        "version": "massive-local-approval-v1",
        "account_evidence_hash": "a" * 64,
        "usage_permission_hash": "b" * 64,
        "entitlement_start": "2026-10-01",
        "entitlement_end": "2026-10-29",
        "actions_start": "2026-10-01",
        "actions_end": "2026-10-29",
        "revision_policy": "append-only-reacquisition-v1",
    }
    path.write_text(json.dumps(payload))
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        publish.load_approval(path)

    path.chmod(0o600)
    approval = publish.load_approval(path)
    assert approval.entitlement_start.isoformat() == "2026-10-01"

    payload["revision_policy"] = "overwrite-existing"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        publish.load_approval(path)
