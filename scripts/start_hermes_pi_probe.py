"""Start the locked Hermes probe using the user's selected private pi route."""

import json
import os
from pathlib import Path

if os.environ.get("YTA_PI_MODEL_PROBE") != "1":
    raise SystemExit("Explicit pi model probe mode required")
route = json.loads(Path("/opt/data/pi-route.json").read_text())
home = Path("/opt/data/profiles/alpha-orchestrator")
home.mkdir(parents=True, exist_ok=True, mode=0o700)
home.chmod(0o700)
config = {
    "gateway": {"standalone": True},
    "model": {
        "default": route["model"],
        "provider": "custom",
        "base_url": route["base_url"],
        "api_mode": route["api_mode"],
        "context_length": route["context_length"],
    },
    "custom_providers": [
        {
            "name": "pi-selected",
            "base_url": route["base_url"],
            "api_mode": route["api_mode"],
            "extra_headers": route["headers"],
        }
    ],
    "tools": {"tool_search": {"enabled": "off"}},
    "platform_toolsets": {"api_server": ["yta-finance"], "cli": ["no_mcp"]},
    "agent": {
        "disabled_toolsets": ["x_search"],
        "max_turns": 4,
        "gateway_timeout": 120,
        "reasoning_effort": False if route["thinking"] == "off" else route["thinking"],
    },
    "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
    "mcp_servers": {
        "yta-finance": {
            "url": "http://finance-tools:8200/mcp",
            "headers": {"Authorization": "Bearer " + os.environ["YTA_FINANCE_MCP_TOKEN"]},
            "sampling": {"enabled": False},
            "connect_timeout": 10,
            "timeout": 10,
        }
    },
}
with os.fdopen(
    os.open(home / "config.yaml", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
) as file:
    json.dump(config, file)
os.environ.update(
    {
        "HERMES_HOME": str(home),
        "OPENAI_API_KEY": route["api_key"],
        "OPENAI_BASE_URL": route["base_url"],
    }
)
os.execv(
    "/opt/hermes/bin/hermes",
    ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"],
)
