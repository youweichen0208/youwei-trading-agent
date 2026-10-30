"""Bootstrap only an isolated Hermes protocol fixture; all model data is synthetic."""

import json
import os
from pathlib import Path

if os.environ.get("YTA_MODEL_FIXTURE_MODE") != "REPLAY":
    raise SystemExit("Explicit REPLAY fixture mode required")
home = Path("/opt/data/profiles/alpha-orchestrator")
home.mkdir(parents=True, exist_ok=True, mode=0o700)
home.chmod(0o700)
config = {
    # Locked Hermes compatibility option for this isolated single-profile probe.
    # This does not qualify production profile multiplexing or isolation.
    "gateway": {"standalone": True},
    # Exercise the two explicit MCP schemas; progressive disclosure is a separate probe.
    "tools": {"tool_search": {"enabled": "off"}},
    "model": {
        "default": "yta-protocol-fixture",
        "provider": "custom",
        "base_url": "http://model-fixture:8300/v1",
        "api_mode": "chat_completions",
    },
    "platform_toolsets": {"api_server": ["yta-finance"], "cli": ["no_mcp"]},
    "agent": {"disabled_toolsets": ["x_search"], "max_turns": 6, "gateway_timeout": 30},
    "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
    "mcp_servers": {
        "yta-finance": {
            "url": "http://finance-tools:8200/mcp",
            "headers": {"Authorization": "Bearer " + os.environ["YTA_FINANCE_MCP_TOKEN"]},
            "sampling": {"enabled": False},
            "timeout": 10,
        }
    },
}
path = home / "config.yaml"
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
    json.dump(config, file)
os.environ["HERMES_HOME"] = str(home)
os.execv(
    "/opt/hermes/bin/hermes",
    ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"],
)
