"""Prepare real Alpha/PG REPLAY draft context and a genuine unavailable-provider response."""

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from backup_local import private_file
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr
from yta_alpha.api import create_app
from yta_alpha.models import DataMode
from yta_alpha.settings import Settings


def main():
    target = Path(".local/report-model-input.json")
    if target.exists():
        raise ValueError("Existing probe context must be reused")
    url = os.environ["YTA_TEST_DATABASE_URL"]
    config = conninfo_to_dict(url)
    assert config["dbname"].endswith("_test") and config["host"] == "127.0.0.1"
    assert config["port"] == "54329" and config["user"] == "yta_app"
    owner, request = uuid4(), uuid4()
    with psycopg.connect(url) as connection:
        connection.execute(
            "INSERT INTO yta.users(id,email) VALUES (%s,%s)", (owner, f"{owner}@report-model.test")
        )
    token = secrets.token_urlsafe(32)
    settings = Settings(
        _env_file=None,
        env="test",
        data_mode=DataMode.REPLAY,
        database_url=SecretStr(url),
        alpha_token=SecretStr(token),
    )
    headers = {"Authorization": "Bearer " + token, "X-YTA-Owner-ID": str(owner)}
    with TestClient(create_app(settings)) as client:
        responses = [
            client.get(path, headers=headers)
            for path in ("/internal/v1/market/evidence", "/internal/v1/market/indicators")
        ]
        assert all(response.status_code == 200 for response in responses)
        envelope, indicators = (response.json() for response in responses)
        response = client.post(
            "/internal/v1/prediction-drafts", headers=headers, json={"request_id": str(request)}
        )
        assert response.status_code == 200
        draft = response.json()
    snapshot = envelope["snapshot"]
    assert (
        envelope["data_snapshot_hash"]
        == indicators["data_snapshot_hash"]
        == draft["forecast"]["data_snapshot_hash"]
    )
    available = max(
        (item["available_at"] for item in snapshot["bars"] + snapshot["splits"]),
        key=lambda value: datetime.fromisoformat(value),
    )
    evidence = [
        {
            "tool_call_id": str(uuid4()),
            "tool": tool,
            "source_id": snapshot["source_id"],
            "data_snapshot_hash": envelope["data_snapshot_hash"],
            "available_at": available,
        }
        for tool in ("market_snapshot", "indicators")
    ]
    ready = {
        "data_mode": "REPLAY",
        "as_of": snapshot["as_of"],
        "evidence": evidence,
        "draft": {
            "draft_id": draft["draft_id"],
            **{key: draft["forecast"][key] for key in ("data_snapshot_hash", "data_mode", "as_of")},
        },
        "failure_code": None,
    }
    live = settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    cutoff = datetime.now(UTC).isoformat()
    with TestClient(create_app(live)) as client:
        response = client.get("/internal/v1/market/evidence", headers=headers)
        assert (
            response.status_code == 503 and response.json()["error"]["code"] == "DATA_UNAVAILABLE"
        )
    unavailable = {
        "data_mode": "LIVE_RESEARCH",
        "as_of": cutoff,
        "evidence": [],
        "draft": None,
        "failure_code": "DATA_UNAVAILABLE",
    }
    with psycopg.connect(url) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.predictions WHERE owner_id=%s", (owner,)
            ).fetchone()[0]
            == 0
        )
    with private_file(target) as output:
        output.write(
            json.dumps(
                {"owner_id": str(owner), "cases": {"ready": ready, "unavailable": unavailable}}
            ).encode()
        )
    print("Prepared Alpha evidence/draft and unavailable response; no frozen prediction")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Report context preparation failed (private details not echoed)") from None
