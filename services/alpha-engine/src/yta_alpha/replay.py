"""Run the documented synthetic closed loop in a development database."""

import json
from uuid import UUID

from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_observation, replay_snapshot
from yta_alpha.models import DataMode
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore


def main() -> None:
    settings = Settings()
    if settings.env != "development" or settings.data_mode != DataMode.REPLAY:
        raise SystemExit("Synthetic replay is allowed only in development / REPLAY")
    if settings.database_url is None:
        raise SystemExit("YTA_DATABASE_URL is required")
    calendar = TradingCalendar()
    store = ResearchStore(settings.database_url.get_secret_value(), calendar, settings.git_sha)
    owner = UUID("00000000-0000-4000-8000-000000000001")
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO yta.users(id, email) VALUES (%s, 'local-replay@yta.invalid') "
            "ON CONFLICT DO NOTHING",
            (owner,),
        )
    snapshot = replay_snapshot(calendar)
    draft = store.create_draft(owner, snapshot, snapshot.as_of)
    frozen = store.freeze(
        owner, UUID(draft["draft_id"]), draft["draft_id"], confirmed=True, now=snapshot.as_of
    )
    observation = replay_observation(calendar)
    settled = store.settle_prediction(
        owner, UUID(frozen["prediction_id"]), observation, now=observation.as_of
    )
    print(
        json.dumps(
            {
                "data_mode": "REPLAY",
                "source": "synthetic fixture; not real NVDA market data",
                "prediction_id": settled["prediction_id"],
                "status": settled["status"],
                "expected_return": settled["forecast"]["expected_return"],
                "outcome": settled["outcomes"][-1]["result"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
