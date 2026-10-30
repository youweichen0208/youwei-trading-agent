"""Two credential-free profiles for isolated HTTP ownership checks."""

import json
import os
from pathlib import Path

if os.environ.get("YTA_HERMES_PROFILE_PROBE") != "1":
    raise SystemExit("Explicit isolated Profile probe required")
home = Path("/opt/data")
for name, key in (
    ("default", os.environ["API_SERVER_KEY"]),
    ("alpha-orchestrator", os.environ["YTA_PROFILE_PROBE_ALPHA_TOKEN"]),
):
    directory = home if name == "default" else home / "profiles" / name
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    config = {
        "gateway": {"multiplex_profiles": True},
        "platform_toolsets": {"api_server": ["no_mcp"], "cli": ["no_mcp"]},
        "mcp_servers": {},
        "agent": {"disabled_toolsets": ["x_search"], "max_turns": 2, "gateway_timeout": 30},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
    }
    for filename, content in (
        ("config.yaml", json.dumps(config)),
        (".env", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=" + key + "\n"),
    ):
        with os.fdopen(
            os.open(directory / filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as file:
            file.write(content)
os.execv(
    "/opt/hermes/bin/hermes",
    ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"],
)
