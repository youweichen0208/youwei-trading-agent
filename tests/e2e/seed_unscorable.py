"""Create a synthetic evidence case for the browser test, only in a dedicated test DB."""

import os
import sys
from urllib.parse import urlparse
from uuid import UUID

from yta_alpha.calendar import TradingCalendar
from yta_alpha.fixtures import replay_observation, replay_snapshot
from yta_alpha.resolution import UnscorableEvidence, record_unscorable
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
observation = replay_observation(calendar)
evidence = UnscorableEvidence(
    data_mode="REPLAY",
    target_session=observation.bars[0].session,
    reason="TARGET_HALTED_NO_CLOSE",
    provider="synthetic-exchange-notice",
    source_id="yta:fixture:target-halt",
    source_document_sha256="b" * 64,
    statement="合成测试证据：目标交易日全天停牌，没有符合口径的收盘价格。",
    published_at=observation.as_of,
    available_at=observation.as_of,
    retrieved_at=observation.as_of,
)
record_unscorable(store, owner, UUID(frozen["prediction_id"]), evidence, now=observation.as_of)
print(frozen["prediction_id"])
