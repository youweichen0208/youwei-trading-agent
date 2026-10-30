"""Only the default test Profile receives the user's pi model credential."""

import json
import os
from pathlib import Path

if os.environ.get("YTA_HERMES_SECRET_PROBE") != "1":
    raise SystemExit("Explicit model-secret probe required")
route = json.loads(Path("/opt/data/pi-route.json").read_text())
for name, api_token in (
    ("default", os.environ["API_SERVER_KEY"]),
    ("alpha-orchestrator", os.environ["YTA_PROFILE_PROBE_ALPHA_TOKEN"]),
):
    home = Path("/opt/data") if name == "default" else Path("/opt/data/profiles") / name
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    config = {
        "gateway": {"multiplex_profiles": True},
        "model": {
            "default": route["model"],
            "provider": "custom",
            "base_url": route["base_url"],
            "api_mode": route["api_mode"],
            "context_length": route["context_length"],
        },
        "platform_toolsets": {"api_server": ["no_mcp"], "cli": ["no_mcp"]},
        "mcp_servers": {},
        "agent": {
            "disabled_toolsets": ["x_search"],
            "max_turns": 2,
            "gateway_timeout": 90,
            "reasoning_effort": False,
        },
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
    }
    env = "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=" + api_token + "\n"
    if name == "default":
        env += "OPENAI_API_KEY=" + json.dumps(route["api_key"]) + "\n"
        env += "OPENAI_BASE_URL=" + json.dumps(route["base_url"]) + "\n"
        config["custom_providers"] = [
            {
                "name": "pi-selected",
                "base_url": route["base_url"],
                "api_mode": route["api_mode"],
                "extra_headers": route["headers"],
            }
        ]
    for filename, content in (("config.yaml", json.dumps(config)), (".env", env)):
        with os.fdopen(
            os.open(home / filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as file:
            file.write(content)
os.execv(
    "/opt/hermes/bin/hermes",
    ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"],
)
