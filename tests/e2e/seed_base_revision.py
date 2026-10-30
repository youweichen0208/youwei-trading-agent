"""Seed a two-version synthetic settlement for browser verification in a test database only."""

import os
import sys
from urllib.parse import urlparse
from uuid import UUID

from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_base_revision, replay_observation, replay_snapshot
from yta_alpha.store import ResearchStore

url = os.environ.get(
    "YTA_TEST_DATABASE_URL", "postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test"
)
if not urlparse(url).path.endswith("_test"):
    raise SystemExit("Dedicated test database required")
owner = UUID(sys.argv[1])
calendar = TradingCalendar()
store = ResearchStore(url, calendar, "a" * 40)
snapshot = replay_snapshot(calendar)
draft = store.create_draft(owner, snapshot, snapshot.as_of)
frozen = store.freeze(
    owner, UUID(draft["draft_id"]), f"e2e-{draft['draft_id']}", confirmed=True, now=snapshot.as_of
)
pid = UUID(frozen["prediction_id"])
observed = replay_observation(calendar)
store.settle_prediction(owner, pid, observed, now=observed.as_of)
revision = replay_base_revision(calendar)
store.settle_prediction(
    owner, pid, observed, now=revision.as_of, base_revision=revision, supersedes_version=1
)
print(pid)
