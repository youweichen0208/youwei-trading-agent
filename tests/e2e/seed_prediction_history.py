"""Seed synthetic prediction history for browser verification in a test database only."""

import os
import sys
from urllib.parse import urlparse
from uuid import UUID

from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_snapshot
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
for _ in range(28):
    draft = store.create_draft(owner, snapshot, snapshot.as_of)
    store.freeze(
        owner,
        UUID(draft["draft_id"]),
        f"page-{draft['draft_id']}",
        confirmed=True,
        now=snapshot.as_of,
    )
print("28")
