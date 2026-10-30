"""Check scoped secret/header lookup even with an ambient credential; never print values."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    if os.environ.get("YTA_HERMES_SECRET_PROBE") != "1":
        raise RuntimeError("Explicit probe required")
    route = json.loads(Path("/opt/data/pi-route.json").read_text())
    os.environ["OPENAI_API_KEY"] = route["api_key"]
    from agent.secret_scope import UnscopedSecretError, get_secret_str, set_multiplex_active
    from gateway.run import _profile_runtime_scope
    from hermes_cli.config import get_custom_provider_extra_headers, load_config
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions

    set_multiplex_active(True)
    try:
        get_secret_str("OPENAI_API_KEY")
    except UnscopedSecretError:
        pass
    else:
        raise RuntimeError("Unscoped credential lookup did not fail closed")

    def check(index):
        owner = index % 2 == 0
        home = Path("/opt/data") if owner else Path("/opt/data/profiles/alpha-orchestrator")
        with _profile_runtime_scope(home):
            key = get_secret_str("OPENAI_API_KEY")
            assert key == (route["api_key"] if owner else "")
            headers = get_custom_provider_extra_headers(route["base_url"])
            assert headers == (route["headers"] if owner else {})
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(check, range(48)))
    for home in (Path("/opt/data"), Path("/opt/data/profiles/alpha-orchestrator")):
        with _profile_runtime_scope(home):
            selected = sorted(_get_platform_tools(load_config(), "api_server"))
            assert selected == []
            definitions = get_tool_definitions(
                enabled_toolsets=selected, disabled_toolsets=["x_search"], quiet_mode=True
            )
            assert definitions == []
    print(
        json.dumps(
            {
                "ambient_key_not_inherited": True,
                "profile_headers_isolated": True,
                "unscoped_read_rejected": True,
                "concurrent_scope_checks": 48,
                "both_profiles_tool_definitions_empty": True,
                "model_requests": 0,
                "qualification_status": "UNVERIFIED",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            json.dumps({"error": "SECRET_LOOKUP_PROBE_FAILED", "type": type(error).__name__})
        ) from None
