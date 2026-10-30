"""Check bootstrap tool selection inside the locked Hermes image, without executing tools."""

import copy
import json
import os
import sys

import hermes_yaml as yaml
from hermes_cli.tools_config import _get_platform_tools


def check(config: dict) -> dict:
    observed = {}
    original_xai = os.environ.pop("XAI_API_KEY", None)
    try:
        for platform in ("api_server", "cli"):
            candidate = copy.deepcopy(config)
            for scenario in ("baseline", "unexpected_mcp", "credential_available"):
                if scenario == "unexpected_mcp":
                    candidate["mcp_servers"] = {"unexpected": {"command": "/never-execute"}}
                if scenario == "credential_available":
                    os.environ["XAI_API_KEY"] = "fixture-only-not-a-real-credential"
                enabled = sorted(_get_platform_tools(candidate, platform))
                if enabled:
                    raise RuntimeError(f"Bootstrap unexpectedly enables toolsets: {enabled}")
                observed[f"{platform}:{scenario}"] = enabled
            os.environ.pop("XAI_API_KEY", None)
    finally:
        if original_xai is not None:
            os.environ["XAI_API_KEY"] = original_xai
    return {
        "checks": observed,
        "status": "bootstrap_selection_passed",
        "integration_status": "UNVERIFIED",
        "scope": "Toolset resolver only; no agent, tool, MCP process or model request executed",
    }


if __name__ == "__main__":
    with open(sys.argv[1]) as source:
        print(json.dumps(check(yaml.safe_load(source))))
