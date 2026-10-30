import re

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from yta_alpha.api import create_app
from yta_alpha.forecast import snapshot_hash
from yta_alpha.models import DataMode, MarketSnapshot
from yta_alpha.settings import Settings

SERVICE = "fixture-service-credential-distinct-123456789"
TOOLS = "fixture-read-only-tools-credential-123456789"
PATHS = ("/internal/v1/tools/market-snapshot", "/internal/v1/tools/indicators")


def configured(**kwargs):
    return Settings(
        _env_file=None, alpha_token=SecretStr(SERVICE), alpha_tools_token=SecretStr(TOOLS), **kwargs
    )


def test_tool_reads_preserve_evidence_decimals_and_missing_values(calendar):
    client = TestClient(create_app(configured(), calendar=calendar))
    headers = {"Authorization": f"Bearer {TOOLS}"}
    snapshot_response = client.get(PATHS[0], headers=headers)
    assert snapshot_response.status_code == 200
    body = snapshot_response.json()
    snapshot = MarketSnapshot.model_validate(body["snapshot"])
    assert body["data_snapshot_hash"] == snapshot_hash(snapshot)
    assert snapshot.data_mode == DataMode.REPLAY
    assert snapshot.provider and snapshot.source_id and snapshot.as_of
    assert isinstance(body["snapshot"]["bars"][0]["close"], str)
    response = client.get(PATHS[1], headers=headers)
    assert response.status_code == 200
    chart = response.json()
    assert chart["data_snapshot_hash"] == body["data_snapshot_hash"]
    assert chart["series"]["SMA20"][0] is None
    assert isinstance(chart["series"]["SMA20"][-1], str)


def test_tool_token_cannot_access_any_other_internal_route(calendar):
    app = create_app(configured(), calendar=calendar)
    client = TestClient(app)
    covered = 0
    for route in app.routes:
        if not route.path.startswith("/internal/") or route.path in (
            *PATHS,
            "/internal/v1/tools/observations",
        ):
            continue
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000001", route.path)
        for method in route.methods:
            response = client.request(
                method, path, headers={"Authorization": f"Bearer {TOOLS}"}, json={}
            )
            assert response.status_code == 401, (method, path, response.text)
            assert TOOLS not in response.text
            covered += 1
    assert covered >= 10


@pytest.mark.parametrize("token", [None, SERVICE, "incorrect-credential"])
def test_tool_routes_reject_other_credentials(calendar, token):
    client = TestClient(create_app(configured(), calendar=calendar))
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    for path in PATHS:
        assert client.get(path, headers=headers).status_code == 401


def test_tool_routes_disabled_without_separate_token(calendar, monkeypatch):
    monkeypatch.delenv("YTA_ALPHA_TOOLS_TOKEN", raising=False)
    settings = Settings(_env_file=None, alpha_token=SecretStr(SERVICE))
    client = TestClient(create_app(settings, calendar=calendar))
    for path in PATHS:
        assert client.get(path, headers={"Authorization": f"Bearer {TOOLS}"}).status_code == 401


def test_real_mode_never_substitutes_replay(calendar):
    client = TestClient(
        create_app(
            configured(data_mode=DataMode.LIVE_RESEARCH, git_sha="a" * 40), calendar=calendar
        )
    )
    for path in PATHS:
        response = client.get(path, headers={"Authorization": f"Bearer {TOOLS}"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
        assert "bars" not in response.json()


@pytest.mark.parametrize("token", [SERVICE, "replace-with-" + "x" * 32, "x" * 32 + " "])
def test_tool_credential_must_be_distinct_and_valid(token):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, alpha_token=SecretStr(SERVICE), alpha_tools_token=SecretStr(token))
    assert token not in str(error.value)
