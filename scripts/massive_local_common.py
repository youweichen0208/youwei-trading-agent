import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def database_url(root: Path = ROOT) -> str:
    state = json.loads((root / ".local/massive-live-state.json").read_text())
    return "postgresql://yta_app:local-yta-app@127.0.0.1:54329/" + state["database"]


def provider_token(root: Path = ROOT) -> str:
    return (root / ".local/massive.env").read_text().strip().split("=", 1)[1]
