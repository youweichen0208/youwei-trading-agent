import json
import uuid

import pytest
from alpha_hermes.bootstrap import gateway_environment, prepare, runtime_config
from alpha_hermes.bridge import BridgeError
from alpha_hermes.model_transport import anthropic_auth_style


def specification(role="research"):
    return {"version": 1, "ownerId": str(uuid.uuid4()), "profileId": str(uuid.uuid4()),
            "role": role, "gatewayOrigin": "http://private-tools:3410",
            "model": {"name": "test-only-model", "baseUrl": "https://model.invalid/v1",
                      "apiMode": "chat_completions"}}


def home_for(tmp_path, spec):
    home = tmp_path / spec["profileId"]
    home.mkdir(mode=0o700)
    (home / ".alpha-profile.json").write_text(json.dumps({key: spec[key] for key in
        ("version", "ownerId", "profileId", "role")}))
    return home


def test_bootstrap_persists_only_nonsecret_config_and_preserves_memory(tmp_path):
    spec = specification()
    home = home_for(tmp_path, spec)
    (home / "memory.txt").write_text("private research memory")
    secret = {"apiKey": "1" * 64, "modelApiKey": "2" * 64}
    env = prepare(home, spec, secret)
    config = (home / "config.yaml").read_text()
    assert secret["apiKey"] not in config and secret["modelApiKey"] not in config
    parsed = json.loads(config)
    assert parsed["platform_toolsets"] == {"api_server": ["alpha_lab"]}
    assert parsed["tools"]["tool_search"]["enabled"] == "off"
    assert not parsed["tools"]["connectors"]["enabled"]
    assert parsed["max_concurrent_sessions"] == 1
    assert not parsed["auth"]["adopt_external_logins"]
    assert env["API_SERVER_KEY"] == secret["apiKey"]
    assert env["ALPHA_ROLE"] == "research"
    assert (home / "config.yaml").stat().st_mode & 0o777 == 0o600
    prepare(home, spec, secret)
    assert (home / "memory.txt").read_text() == "private research memory"


def test_bootstrap_refuses_foreign_or_unmarked_home_before_writes(tmp_path):
    original = specification()
    home = home_for(tmp_path, original)
    secret = {"apiKey": "1" * 64, "modelApiKey": "2" * 64}
    for overrides in ({"ownerId": str(uuid.uuid4())}, {"profileId": str(uuid.uuid4())},
                      {"role": "portfolio"}):
        with pytest.raises(BridgeError, match="identity_mismatch"):
            prepare(home, original | overrides, secret)
        assert not (home / "config.yaml").exists()
    (home / ".alpha-profile.json").unlink()
    with pytest.raises(BridgeError, match="invalid_profile_file"):
        prepare(home, original, secret)
    assert not (home / ".alpha-private").exists()


def test_bootstrap_refuses_credentials_in_urls_and_unexpected_identity_fields():
    spec = specification()
    with pytest.raises(BridgeError, match="invalid_runtime_origin"):
        runtime_config(spec | {"gatewayOrigin": "https://user:secret@tools.invalid"})
    with pytest.raises(BridgeError, match="invalid_profile_spec"):
        runtime_config(spec | {"capability": "1" * 64})
    with pytest.raises(BridgeError, match="invalid_gateway_origin"):
        runtime_config(spec | {"gatewayOrigin": "http://127.0.0.1:3410"})


def test_gateway_process_receives_only_this_profiles_credentials():
    inherited = {"PATH": "/bin", "HERMES_HOME": "/opt/data", "DATABASE_URL": "db-secret",
                 "ALPACA_DATA_KEY": "market-secret", "TOOL_CAPABILITY_KEY": "worker-secret",
                 "OPENROUTER_API_KEY": "another-profile-secret", "PYTHONPATH": "/untrusted"}
    credentials = {"API_SERVER_KEY": "own-key", "ALPHA_MODEL_API_KEY": "own-model-key"}
    assert gateway_environment(inherited, credentials) == {
        "PATH": "/bin", "HERMES_HOME": "/opt/data", **credentials,
    }


def test_bootstrap_replaces_generated_dotenv_key_with_mounted_identity(tmp_path):
    spec = specification()
    home = home_for(tmp_path, spec)
    secret = {"apiKey": "1" * 64, "modelApiKey": "provider-key-${UNRELATED}-literal"}
    (home / ".env").write_text("API_SERVER_KEY=" + "9" * 64 + "\nOPENROUTER_API_KEY=stale\n")
    (home / "memory.txt").write_text("keep this memory")
    env = prepare(home, spec, secret)
    assert (home / ".env").read_text() == "API_SERVER_KEY=" + secret["apiKey"] + "\n"
    assert (home / ".env").stat().st_mode & 0o777 == 0o600
    assert secret["modelApiKey"] not in (home / ".env").read_text()
    assert env["ALPHA_MODEL_API_KEY"] == secret["modelApiKey"]
    assert (home / "memory.txt").read_text() == "keep this memory"
    prepare(home, spec, secret)
    assert (home / ".env").read_text() == "API_SERVER_KEY=" + secret["apiKey"] + "\n"


def test_bootstrap_refuses_dotenv_symlink_before_writes(tmp_path):
    spec = specification()
    home = home_for(tmp_path, spec)
    foreign = tmp_path / "unrelated.env"
    foreign.write_text("keep this file")
    (home / ".env").symlink_to(foreign)
    with pytest.raises(BridgeError, match="invalid_profile_file"):
        prepare(home, spec, {"apiKey": "1" * 64, "modelApiKey": "2" * 64})
    assert foreign.read_text() == "keep this file"
    assert not (home / "config.yaml").exists()


def test_anthropic_credentials_are_scoped_to_configured_route(tmp_path, monkeypatch):
    spec = specification()
    spec["model"].update(apiMode="anthropic_messages", authMode="bearer")
    home = home_for(tmp_path, spec)
    secret = {"apiKey": "1" * 64, "modelApiKey": "2" * 64}
    env = prepare(home, spec, secret)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = json.loads((home / "config.yaml").read_text())
    assert config["model"]["api_mode"] == "anthropic_messages"
    assert secret["modelApiKey"] not in json.dumps(config)
    assert secret["modelApiKey"] not in (home / ".env").read_text()
    assert anthropic_auth_style("2" * 64, spec["model"]["baseUrl"], "api_key") == "bearer"
    assert anthropic_auth_style("2" * 64, "https://other.invalid/v1", "api_key") == "api_key"
    assert anthropic_auth_style("3" * 64, spec["model"]["baseUrl"], "api_key") == "api_key"
    monkeypatch.delenv("ALPHA_MODEL_AUTH_MODE")
    assert anthropic_auth_style("2" * 64, spec["model"]["baseUrl"], "api_key") == "api_key"


@pytest.mark.parametrize("mode,auth", [("responses", "bearer"),
                                       ("anthropic_messages", "unknown")])
def test_bootstrap_rejects_unsupported_auth_modes(mode, auth):
    spec = specification()
    spec["model"].update(apiMode=mode, authMode=auth)
    with pytest.raises(BridgeError, match="invalid_model_config"):
        runtime_config(spec)
