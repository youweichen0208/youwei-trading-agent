"""Explicit Anthropic authentication for this Profile's configured endpoint only."""

import os


def anthropic_auth_style(api_key, base_url, default: str) -> str:
    mode = os.environ.get("ALPHA_MODEL_AUTH_MODE")
    configured_url = os.environ.get("ALPHA_MODEL_BASE_URL", "")
    configured_key = os.environ.get("ALPHA_MODEL_API_KEY", "")
    if (mode in {"api_key", "bearer"} and configured_url and configured_key
            and isinstance(api_key, str) and api_key == configured_key
            and isinstance(base_url, str)
            and base_url.rstrip("/") == configured_url.rstrip("/")):
        return mode
    return default
