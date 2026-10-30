"""Export internal OpenAPI and a synthetic cross-language response fixture."""

import argparse
import json
from pathlib import Path

from pydantic import SecretStr
from yta_alpha.api import create_app
from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.forecast import forecast
from yta_alpha.indicators import indicators
from yta_alpha.models import Outcome
from yta_alpha.resolution import UnscorableEvidence
from yta_alpha.settings import Settings

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
calendar = TradingCalendar()
snapshot = replay_snapshot(calendar, count=21)
settings = Settings(
    _env_file=None, alpha_token=SecretStr("schema-generation-not-a-live-token-12345")
)
artifacts = {
    "packages/contracts/fixtures/chart.json": indicators(snapshot, calendar),
    "packages/contracts/settled-outcome.schema.json": Outcome.model_json_schema(),
    "packages/contracts/unscorable-evidence.schema.json": UnscorableEvidence.model_json_schema(),
    "packages/contracts/alpha.openapi.json": create_app(settings, calendar=calendar).openapi(),
    "packages/contracts/fixtures/forecast.json": forecast(
        snapshot, now=snapshot.as_of, calendar=calendar, git_sha="0" * 40
    ).model_dump(mode="json"),
}
for name, content in artifacts.items():
    file = root / name
    expected = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not file.exists() or file.read_text() != expected:
            raise SystemExit(
                f"Contract drift: run uv run python scripts/export_contracts.py ({name})"
            )
    else:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(expected)
    print(name)
