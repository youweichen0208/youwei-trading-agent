"""Copy only pi's selected model route into a private, exclusive probe file."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def selected_route(root):
    settings = json.loads((root / "settings.json").read_text())
    providers = json.loads((root / "models.json").read_text())["providers"]
    provider = settings["defaultProvider"]
    model = settings["defaultModel"]
    route = providers[provider]
    selected = next(item for item in route["models"] if item["id"] == model)
    if route["api"] != "anthropic-messages":
        raise ValueError("Unsupported pi transport")
    url = urlsplit(route["baseUrl"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Expected a credential-free HTTPS endpoint")
    key = route["apiKey"]
    if not isinstance(key, str) or not key or key.startswith("!"):
        raise ValueError("Expected a literal or environment credential")
    key = os.environ.get(key, key)
    headers = route.get("headers", {})
    if any(not isinstance(value, str) or value.startswith("!") for value in headers.values()):
        raise ValueError("Unsupported dynamic header")
    return {
        "source_provider": provider,
        "model": model,
        "base_url": route["baseUrl"],
        "api_mode": "anthropic_messages",
        "api_key": key,
        "headers": headers,
        "context_length": selected["contextWindow"],
        "thinking": settings.get("defaultThinkingLevel", "off"),
    }


def main():
    route = selected_route(Path.home() / ".pi/agent")
    destination = Path(".local/pi-model-probe.json")
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(route, file)
    print(
        json.dumps(
            {
                "provider": route["source_provider"],
                "model": route["model"],
                "private_file_created": True,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Pi model preparation failed; existing private files are never overwritten"
        ) from None
