import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from yta_alpha.api import create_app
from yta_alpha.settings import Settings

TOKEN = "test-service-token-not-a-real-secret-12345"


def test_service_auth_contract_and_error_redaction(snapshot, calendar):
    settings = Settings(_env_file=None, alpha_token=SecretStr(TOKEN))
    client = TestClient(create_app(settings, lambda: snapshot.as_of, calendar))
    body = {"snapshot": snapshot.model_dump(mode="json"), "horizon_sessions": 5}
    denied = client.post("/internal/v1/predict", json=body)
    assert denied.status_code == 401
    assert denied.json()["error"]["request_id"].startswith("req_")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.post("/internal/v1/predict", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["expected_return"] == "0.005000000000"
    assert response.json()["data_mode"] == "REPLAY"
    body["now"] = "secret-that-must-not-be-reflected"
    invalid = client.post("/internal/v1/predict", headers=headers, json=body)
    assert invalid.status_code == 422
    assert "secret-that-must-not-be-reflected" not in invalid.text
    assert "body.now" in invalid.text


def test_unsupported_horizon_and_cross_mode(snapshot, calendar):
    settings = Settings(_env_file=None, alpha_token=SecretStr(TOKEN))
    client = TestClient(create_app(settings, lambda: snapshot.as_of, calendar))
    body = {"snapshot": snapshot.model_dump(mode="json"), "horizon_sessions": 20}
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/internal/v1/predict", json=body, headers=headers).status_code == 422
    body["horizon_sessions"] = 5
    body["snapshot"]["data_mode"] = "LIVE_RESEARCH"
    response = client.post("/internal/v1/predict", json=body, headers=headers)
    assert response.json()["error"]["code"] == "DATA_MODE_MISMATCH"


def test_production_rejects_replay_and_example_tokens():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="production", alpha_token=SecretStr(TOKEN))
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None, alpha_token=SecretStr("replace-with-a-long-enough-token-but-invalid")
        )
    assert "replace-with-a-long-enough-token-but-invalid" not in str(error.value)


def test_readiness_is_private_and_redacts_dependency_failures(calendar, monkeypatch):
    import yta_alpha.readiness as probes

    settings = Settings(_env_file=None, alpha_token=SecretStr(TOKEN), database_url=None)
    client = TestClient(create_app(settings, calendar=calendar))
    assert client.get("/health").status_code == 200
    assert client.get("/internal/v1/ready").status_code == 401
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.get("/internal/v1/ready", headers=headers)
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "not_configured"

    def broken(*args, **kwargs):
        raise RuntimeError("connection with secret-token-must-not-leak failed")

    monkeypatch.setattr(probes.psycopg, "connect", broken)
    configured = settings.model_copy(update={"database_url": SecretStr("postgresql://fake/test")})
    response = TestClient(create_app(configured, calendar=calendar)).get(
        "/internal/v1/ready", headers=headers
    )
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"
    assert "secret-token" not in response.text


def test_bff_market_evidence_auth_and_hash(calendar):
    from yta_alpha.forecast import snapshot_hash
    from yta_alpha.models import DataMode, MarketSnapshot

    settings = Settings(
        _env_file=None,
        alpha_token=SecretStr(TOKEN),
        alpha_tools_token=SecretStr("separate-financial-tools-token-12345"),
    )
    with TestClient(create_app(settings, calendar=calendar)) as client:
        path = "/internal/v1/market/evidence"
        assert client.get(path).status_code == 401
        assert (
            client.get(
                path, headers={"Authorization": "Bearer separate-financial-tools-token-12345"}
            ).status_code
            == 401
        )
        response = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        data = response.json()
        snapshot = MarketSnapshot.model_validate(data["snapshot"])
        assert data["data_snapshot_hash"] == snapshot_hash(snapshot)
        assert snapshot.data_mode.value == "REPLAY"
    live = settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    with TestClient(create_app(live, calendar=calendar)) as client:
        assert client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 503


def test_market_freshness_reports_replay_and_live_unavailable(calendar):
    from yta_alpha.models import DataMode

    headers = {"Authorization": f"Bearer {TOKEN}"}
    settings = Settings(_env_file=None, alpha_token=SecretStr(TOKEN))
    path = "/internal/v1/market/freshness"
    with TestClient(create_app(settings, calendar=calendar)) as client:
        assert client.get(path).status_code == 401
        body = client.get(path, headers=headers).json()
        assert body["status"] == "FRESH" and body["data_mode"] == "REPLAY"
        assert body["ttl_seconds"] == 30
        assert (
            body["data_snapshot_hash"]
            == client.get("/internal/v1/market/evidence", headers=headers).json()[
                "data_snapshot_hash"
            ]
        )
    with TestClient(
        create_app(
            settings.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), calendar=calendar
        )
    ) as client:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "UNAVAILABLE"
        assert response.json()["data_snapshot_hash"] is None
        assert client.get("/internal/v1/market/snapshot", headers=headers).status_code == 503


def test_stale_cache_is_metadata_only_across_alpha_research_routes(calendar, snapshot, monkeypatch):
    import yta_alpha.api as module
    from yta_alpha.market_cache import MarketCache

    ticks = [0.0]
    broken = [False]

    def load(*args, **kwargs):
        if broken[0]:
            raise RuntimeError("private provider error")
        return snapshot

    monkeypatch.setattr(module, "replay_snapshot", load)
    monkeypatch.setattr(
        module, "MarketCache", lambda *args: MarketCache(*args, ttl=1, monotonic=lambda: ticks[0])
    )
    settings = Settings(_env_file=None, alpha_token=SecretStr(TOKEN), database_url=None)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Yta-Owner-Id": "00000000-0000-4000-8000-000000000045",
    }
    with TestClient(create_app(settings, calendar=calendar)) as client:
        assert (
            client.get("/internal/v1/market/freshness", headers=headers).json()["status"] == "FRESH"
        )
        broken[0] = True
        ticks[0] = 1
        stale = client.get("/internal/v1/market/freshness", headers=headers)
        assert stale.json()["status"] == "STALE"
        assert "private provider" not in stale.text
        for path in [
            "/internal/v1/market/snapshot",
            "/internal/v1/market/evidence",
            "/internal/v1/market/indicators",
        ]:
            response = client.get(path, headers=headers)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "STALE_DATA"
        response = client.post(
            "/internal/v1/prediction-drafts",
            headers=headers,
            json={"symbol": "NVDA", "request_id": "00000000-0000-4000-8000-000000000145"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "STALE_DATA"
        broken[0] = False
        ticks[0] = 2
        assert (
            client.get("/internal/v1/market/freshness", headers=headers).json()["status"] == "FRESH"
        )


@pytest.mark.parametrize(
    "clock_kind,code",
    [
        ("future", "FUTURE_INFORMATION"),
        ("opening", "FEATURE_WINDOW_CLOSED"),
        ("next_close", "STALE_DATA"),
    ],
)
def test_experimental_live_window_checks_before_model_lookup(snapshot, calendar, clock_kind, code):
    from datetime import timedelta

    from yta_alpha.errors import DomainError
    from yta_alpha.experimental_predictions import ExperimentalRequest, experimental_predict

    class UnusedRegistry:
        def get(self, identity):
            raise AssertionError("Invalid time must be rejected before database/model access")

    next_day = calendar.after(snapshot.bars[-1].session, 1)
    clocks = {
        "future": snapshot.as_of - timedelta(seconds=1),
        "opening": calendar.open_at(next_day),
        "next_close": calendar.close_at(next_day),
    }
    with pytest.raises(DomainError) as error:
        experimental_predict(
            UnusedRegistry(),
            "unused",
            ExperimentalRequest(snapshot=snapshot, horizon_sessions=5),
            calendar=calendar,
            now=clocks[clock_kind],
            replay=False,
        )
    assert error.value.code == code
