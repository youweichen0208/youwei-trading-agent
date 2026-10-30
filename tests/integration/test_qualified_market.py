import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.calendar import TradingCalendar
from yta_alpha.forecast import forecast
from yta_alpha.massive import MassiveCandidate, RawPage
from yta_alpha.migrate import migrate
from yta_alpha.models import DataMode
from yta_alpha.qualified_market import QualifiedMarket

pytestmark = pytest.mark.postgres
START, END = date(2026, 10, 1), date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 22, tzinfo=UTC)


@pytest.fixture(scope="module")
def market():
    owner = os.environ.get("YTA_TEST_DATABASE_URL")
    if not owner:
        pytest.skip("Dedicated PostgreSQL database required")
    config = conninfo_to_dict(owner)
    assert config["dbname"].endswith("_test")
    migrate(owner, Path(__file__).resolve().parents[2] / "infra/migrations")
    with psycopg.connect(owner) as connection:
        connection.execute("GRANT SELECT,INSERT ON yta.market_qualifications TO yta_app")
    config.update(user="yta_app", password="local-yta-app")
    runtime = os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL") or make_conninfo(**config)
    calendar = TradingCalendar()
    days = calendar.sessions_between(START, END)

    def handler(request):
        payload = {"status": "OK", "results": []}
        if request.url.path.startswith("/v2/aggs"):
            payload.update(
                ticker="NVDA",
                adjusted=False,
                results=[
                    {
                        "t": int(datetime.combine(day, datetime.min.time(), UTC).timestamp() * 1000)
                        + 4 * 3600 * 1000,
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100,
                        "v": 1000,
                    }
                    for day in days
                ],
            )
        return httpx.Response(200, json=payload)

    acquisition = asyncio.run(
        MassiveCandidate(
            SecretStr("test"), transport=httpx.MockTransport(handler), clock=lambda: NOW
        ).acquire(START, END)
    )
    digest = AcquisitionStore(runtime).save(
        acquisition, start=START, end=END, data_mode=DataMode.LIVE_RESEARCH
    )
    summaries = tuple(
        RawPage(
            f"/v1/open-close/NVDA/{day}",
            (("adjusted", "false"),),
            NOW,
            json.dumps({"status": "OK", "symbol": "NVDA", "from": str(day), "close": 100}).encode(),
        )
        for day in days
    )
    return owner, runtime, digest, summaries, calendar


def test_concurrent_publication_reproducible_and_raw_remains_unverified(market):
    owner, runtime, digest, summaries, calendar = market
    source = QualifiedMarket(runtime, calendar, DataMode.LIVE_RESEARCH)
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: source.publish(digest, summaries), range(8)))
    assert len(set(ids)) == 1
    snapshot = source.latest(NOW)
    assert snapshot.provider == "massive" and len(snapshot.bars) == 21
    assert all(b.available_at == NOW for b in snapshot.bars)
    prediction = forecast(snapshot, now=NOW, calendar=calendar, git_sha="a" * 40)
    assert source.observation(prediction, NOW + timedelta(days=10)) is None
    with psycopg.connect(owner) as connection:
        assert (
            connection.execute(
                "SELECT qualification_status FROM yta.provider_acquisitions WHERE hash=%s",
                (digest,),
            ).fetchone()[0]
            == "UNVERIFIED"
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM yta.audit_events "
                "WHERE action='market.qualified' AND resource_id=%s",
                (ids[0],),
            ).fetchone()[0]
            == 1
        )
    for url in (owner, runtime):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute("DELETE FROM yta.market_qualifications WHERE hash=%s", (ids[0],))
            connection.rollback()


@pytest.mark.parametrize("failure", ["missing", "close", "scope", "early"])
def test_rejects_invalid_verification(market, failure):
    _, runtime, digest, summaries, calendar = market
    bad = list(summaries)
    if failure == "missing":
        bad.pop()
    elif failure == "close":
        bad[0] = replace(bad[0], body=bad[0].body.replace(b"100", b"101"))
    elif failure == "scope":
        bad[0] = replace(bad[0], query=(("adjusted", "true"),))
    else:
        bad[0] = replace(bad[0], retrieved_at=NOW - timedelta(days=60))
    with pytest.raises(ValueError):
        QualifiedMarket(runtime, calendar, DataMode.LIVE_RESEARCH).publish(digest, tuple(bad))


def test_cohort_and_future_isolation(market):
    from yta_alpha.errors import DomainError

    _, runtime, _, _, calendar = market
    for mode, at in [(DataMode.REPLAY, NOW), (DataMode.LIVE_RESEARCH, NOW - timedelta(days=1))]:
        with pytest.raises(DomainError, match="No qualified"):
            QualifiedMarket(runtime, calendar, mode).latest(at)
