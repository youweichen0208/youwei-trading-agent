"""Validate the mounted Profile identity before starting its single Hermes gateway."""

import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from alpha_hermes.bridge import BindingStore, BridgeError


def read_json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise BridgeError("invalid_profile_file")
    return json.loads(path.read_text())


def runtime_config(spec: dict) -> dict:
    if set(spec) != {"version", "ownerId", "profileId", "role", "model", "gatewayOrigin"}:
        raise BridgeError("invalid_profile_spec")
    if spec["version"] != 1 or spec["role"] not in {"research", "prediction", "portfolio"}:
        raise BridgeError("invalid_profile_spec")
    for name in ("ownerId", "profileId"):
        if str(uuid.UUID(spec[name])) != spec[name]:
            raise BridgeError("invalid_profile_identity")
    model = spec["model"]
    if (not isinstance(model, dict)
            or not {"name", "baseUrl", "apiMode"} <= set(model)
            or set(model) - {"name", "baseUrl", "apiMode", "authMode"}
            or not isinstance(model["name"], str) or not 1 <= len(model["name"]) <= 200
            or model["apiMode"] not in {"chat_completions", "responses", "anthropic_messages"}
            or ("authMode" in model and (model["apiMode"] != "anthropic_messages"
                                        or model["authMode"] not in {"api_key", "bearer"}))):
        raise BridgeError("invalid_model_config")
    for value in (model["baseUrl"], spec["gatewayOrigin"]):
        url = urlsplit(value)
        if (url.scheme not in {"https", "http"} or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise BridgeError("invalid_runtime_origin")
    gateway = urlsplit(spec["gatewayOrigin"])
    if gateway.path not in {"", "/"} or gateway.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise BridgeError("invalid_gateway_origin")
    return {
        "model": {"default": model["name"], "provider": "custom", "base_url": model["baseUrl"],
                  "api_mode": model["apiMode"], "key_env": "ALPHA_MODEL_API_KEY"},
        "fallback_providers": [], "fallback_model": None,
        "toolsets": ["alpha_lab"], "platform_toolsets": {"api_server": ["alpha_lab"]},
        # Native deferred discovery would replace the role catalog with
        # tool_search/tool_call wrappers, which the trusted core rightly denies.
        "tools": {"tool_search": {"enabled": "off"}, "connectors": {"enabled": False}},
        "plugins": {"enabled": ["alpha-lab"], "auto_apply": False, "auto_update_check_hours": 0},
        "platforms": {"api_server": {"enabled": True, "host": "0.0.0.0", "port": 8642,
                                     "direct_model_requests": False, "cors_origins": []}},
        "max_concurrent_sessions": 1, "max_live_sessions": 8,
        "agent": {"max_turns": 60, "run_budget_seconds": 900,
                  "agent_cache": {"max_size": 4}},
        "auth": {"adopt_external_logins": False},
        "security": {"allow_lazy_installs": False, "redact_secrets": True},
        "mcp_servers": {}, "hooks": {}, "hooks_auto_accept": False,
        # Per-Profile memory remains in this home. No other home is mounted.
    }


def write_private(home: Path, name: str, content: str):
    fd, temporary = tempfile.mkstemp(prefix=".alpha-config-", dir=home)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, home / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(home: Path, spec: dict, secret: dict) -> dict[str, str]:
    config = runtime_config(spec)
    if (not isinstance(secret, dict) or set(secret) != {"apiKey", "modelApiKey"}
            or not isinstance(secret["apiKey"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", secret["apiKey"])
            or not isinstance(secret["modelApiKey"], str)
            or not re.fullmatch(r"[^\s\x00-\x1f]{16,4096}", secret["modelApiKey"])):
        raise BridgeError("invalid_profile_credentials")
    identity = {key: spec[key] for key in ("version", "ownerId", "profileId", "role")}
    if home.is_symlink() or read_json(home / ".alpha-profile.json") != identity:
        raise BridgeError("profile_home_identity_mismatch")
    dotenv = home / ".env"
    if dotenv.is_symlink() or (dotenv.exists() and not dotenv.is_file()):
        raise BridgeError("invalid_profile_file")
    # Never initialize an unmarked existing home or silently adopt another role.
    private = home / ".alpha-private"
    BindingStore(private / "bindings.db")
    write_private(home, "config.yaml", json.dumps(config, indent=2) + "\n")
    # The upstream s6 hook generates .env before this unprivileged bootstrap.
    # Native dotenv loads override the process environment, so that generated
    # key would shadow our mounted API identity. This application owns both
    # configuration files in its marked home. Persist only the authoritative
    # hex API key; keep provider keys out of dotenv interpolation and in the
    # filtered process environment. Never adopt seeded provider credentials.
    write_private(home, ".env", "API_SERVER_KEY=" + secret["apiKey"] + "\n")
    return {"ALPHA_ROLE": spec["role"], "ALPHA_BINDING_DB": str(private / "bindings.db"),
            "ALPHA_TOOL_ORIGIN": spec["gatewayOrigin"], "API_SERVER_KEY": secret["apiKey"],
            "ALPHA_MODEL_API_KEY": secret["modelApiKey"],
            "ALPHA_MODEL_BASE_URL": spec["model"]["baseUrl"],
            "ALPHA_MODEL_AUTH_MODE": spec["model"].get("authMode", "")}


def gateway_environment(inherited: dict[str, str], credentials: dict[str, str]) -> dict[str, str]:
    allowed = {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "LD_LIBRARY_PATH",
        "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "XDG_RUNTIME_DIR",
        "HERMES_HOME", "HERMES_RUNTIME_DIR", "HERMES_WRITE_SAFE_ROOT", "HERMES_PYTHON",
        "HERMES_WEB_DIST", "HERMES_TUI_DIR", "PLAYWRIGHT_BROWSERS_PATH",
    }
    return {key: value for key, value in inherited.items() if key in allowed} | credentials


def main():
    try:
        os.umask(0o077)
        if os.getuid() == 0:
            raise BridgeError("profile_gateway_must_be_unprivileged")
        if os.environ.get("HERMES_HOME") != "/opt/data":
            raise BridgeError("unexpected_profile_home")
        spec = read_json(Path(os.environ["ALPHA_PROFILE_SPEC"]))
        secret = read_json(Path(os.environ["ALPHA_PROFILE_SECRET"]))
        env = gateway_environment(dict(os.environ), prepare(Path("/opt/data"), spec, secret))
        # Keep this filtered environment and lifetime in the container CMD.
        # Native s6 redirection starts a different process with container env,
        # losing both role authority and credentials, and auto-restarts it.
        os.execve("/opt/hermes/.venv/bin/hermes",
                  ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"], env)
    except Exception:
        # Never print secret values, JSON decoder excerpts or upstream URLs.
        print("Alpha Profile startup refused: check its private identity/configuration.",
              file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
