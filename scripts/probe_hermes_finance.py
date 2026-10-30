"""Run in the locked Hermes image on the isolated finance-probe tools network."""

import json
import os
import socket
from pathlib import Path

PROFILE = Path("/tmp/yta-finance-probe/profiles/alpha-orchestrator")


def main():
    if os.environ.get("HERMES_HOME") != str(PROFILE):
        raise RuntimeError("Only the disposable finance probe Profile is permitted")
    token = os.environ["YTA_FINANCE_MCP_TOKEN"]
    PROFILE.mkdir(parents=True, exist_ok=False, mode=0o700)
    config = {
        "platform_toolsets": {"api_server": ["yta-finance"], "cli": ["no_mcp"]},
        "agent": {"disabled_toolsets": ["x_search"], "max_turns": 12},
        "mcp_servers": {
            "yta-finance": {
                "url": "http://finance-tools:8200/mcp",
                "headers": {"Authorization": "Bearer " + token},
                "connect_timeout": 10,
                "timeout": 10,
                "sampling": {"enabled": False},
            }
        },
    }
    descriptor = os.open(PROFILE / "config.yaml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        json.dump(config, target)
    try:
        socket.getaddrinfo("alpha", 8100)
    except socket.gaierror:
        pass
    else:
        raise RuntimeError("Hermes probe unexpectedly resolves the private Alpha service")
    from hermes_cli.tools_config import _get_platform_tools
    from tools.mcp_tool_discovery import discover_mcp_tools
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.registry import registry

    try:
        names = sorted(discover_mcp_tools(allowed_mcp_names=["yta-finance"]))
        if set(names) != {"mcp__yta_finance__indicators", "mcp__yta_finance__market_snapshot"}:
            raise RuntimeError("Expected exactly two finance tools")
        selected = sorted(_get_platform_tools(config, "api_server"))
        if selected != ["yta-finance"]:
            raise RuntimeError("Unexpected Profile tool selection")
        results = {}
        for name in names:
            schema = registry.get_schema(name)
            raw = registry.dispatch(name, {})
            result = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(result, dict) or "error" in result:
                raise RuntimeError("Hermes finance tool failed")
            payload = result["result"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            snapshot = payload.get("snapshot", payload)
            if snapshot["data_mode"] != "REPLAY" or not isinstance(
                snapshot["bars"][0]["close"], str
            ):
                raise RuntimeError("Finance tool returned an unexpected data mode or price type")
            if name.endswith("indicators") and payload["series"]["SMA20"][0] is not None:
                raise RuntimeError("Missing indicators must remain null")
            results[name] = {
                "data_mode": snapshot["data_mode"],
                "data_snapshot_hash": payload["data_snapshot_hash"],
                "schema_available": schema is not None,
            }
        if len({r["data_snapshot_hash"] for r in results.values()}) != 1:
            raise RuntimeError("Snapshot and indicators do not share the same evidence hash")
        rejected = registry.dispatch(names[0], {"url": "http://alpha:8100"})
        rejected = json.loads(rejected) if isinstance(rejected, str) else rejected
        if not isinstance(rejected, dict) or "error" not in rejected:
            raise RuntimeError("Unexpected arguments were not rejected")
        print(
            "YTA_FINANCE_PROBE="
            + json.dumps(
                {
                    "registered_tools": names,
                    "selected_toolsets": selected,
                    "tool_results": results,
                    "argument_injection_rejected": True,
                    "alpha_dns_isolated": True,
                    "model_requests_sent": 0,
                    "integration_status": "UNVERIFIED",
                }
            )
        )
    finally:
        shutdown_mcp_servers()


if __name__ == "__main__":
    main()
