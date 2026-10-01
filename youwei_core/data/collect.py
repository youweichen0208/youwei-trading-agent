"""S09a daily collection scheduler (collect_tick).

Phase 1A needs a continuously refreshed evidence base for the frozen
panel + benchmark BEFORE any campaign starts, and ongoing follow-up
afterwards. The existing ``data.tiingo_daily`` job handler can ingest
one (ticker, date-range) call idempotently; what is missing is the
periodic scheduling that submits those jobs. This module provides that
scheduling.

Design constraints (project owner 2026-10-02):

- **Fixed registration reference, never "latest"**: the entrypoint
  config names one ``release_id``; its manifest names exactly one
  ``panel_registration_id`` and one ``benchmark_security_id``. The
  collection set is that panel's ``selected`` list plus the benchmark,
  de-duplicated. A newly registered panel must NOT silently change the
  collection set — the reference is explicit and frozen.
- **Tick timing in America/New_York**: first collection at 17:30 ET on
  trading days (holidays skip new batches; early-close days still
  collect at 17:30). After 17:30, every 30 minutes is a follow-up
  observation slot until 05:30 ET the next day, so a target trading day
  that is not yet complete can be re-queried. Holidays still allow
  those pending follow-up observations to run.
- **Rolling window + gap backfill**: the daily request covers the
  current (just-closed) trading day plus the previous four trading
  days. This rolling window is NOT a historical-completeness guarantee;
  first-start history and outages longer than five trading days are
  separate concerns.
- **Idempotency key binds the observation slot**: tenant + collection
  config version + source + security_id + date range + observation
  slot. Re-submitting the same slot is idempotent; the NEXT slot may
  query the vendor again. An HTTP 200 / non-empty response / created
  == False does not mean the target day is complete — date coverage,
  gaps and data quality are recorded separately.
- **Collection survives stop_new_batches**: stopping NEW predictions
  must not stop collection that serves existing D20/D60 follow-up and
  corrections.

The scheduling only SUBMITS jobs; the actual fetch/parse/store is the
existing ``data.tiingo_daily`` handler (``youwei_core.data.tiingo``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import ET, is_trading_day
from youwei_core.data.tiingo import TiingoClient
from youwei_core.db.meta import (
    panel_registrations,
    research_releases,
    securities,
    security_identities,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run

COLLECT_JOB_KIND = "data.tiingo_daily"
COLLECT_CONFIG_VERSION = "collect-v1"
SOURCE_SLUG = "tiingo"

FIRST_COLLECTION_TIME_ET = time(17, 30)
FOLLOWUP_INTERVAL_MINUTES = 30
FOLLOWUP_UNTIL_ET = time(5, 30)  # next day, America/New_York
ROLLING_TRADING_DAYS = 5


class CollectionConfigError(Exception):
    """The collection configuration is missing or malformed."""


@dataclass(frozen=True)
class CollectionTarget:
    security_id: uuid.UUID
    ticker: str


async def resolve_collection_targets(
    engine: AsyncEngine, release_id: str
) -> list[CollectionTarget]:
    """Resolve the fixed panel + benchmark collection set from ONE named
    release. Never reads "the latest" panel — the reference is explicit.

    Returns de-duplicated targets in deterministic order (sorted by
    ticker). Raises ``CollectionConfigError`` when the release is missing,
    its manifest lacks the required references, the panel is missing, or
    a security cannot be resolved to exactly one ticker.
    """
    if not release_id:
        raise CollectionConfigError("collection release_id is not configured")

    async with engine.begin() as conn:
        release = (
            await conn.execute(
                select(research_releases.c.manifest).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise CollectionConfigError(f"release {release_id!r} not registered")

        panel_id_raw = release.get("panel_registration_id")
        if not panel_id_raw:
            raise CollectionConfigError(
                f"release {release_id!r} manifest lacks panel_registration_id"
            )
        try:
            panel_id = uuid.UUID(str(panel_id_raw))
        except ValueError as exc:
            raise CollectionConfigError(
                f"release {release_id!r} panel_registration_id is not a UUID"
            ) from exc

        references = release.get("references") or {}
        benchmark_raw = references.get("benchmark_security_id")
        if not benchmark_raw:
            raise CollectionConfigError(
                f"release {release_id!r} references lacks benchmark_security_id"
            )
        try:
            benchmark_id = uuid.UUID(str(benchmark_raw))
        except ValueError as exc:
            raise CollectionConfigError(
                f"release {release_id!r} benchmark_security_id is not a UUID"
            ) from exc

        panel = (
            await conn.execute(
                select(panel_registrations.c.selected).where(
                    panel_registrations.c.id == panel_id
                )
            )
        ).scalar_one_or_none()
        if panel is None:
            raise CollectionConfigError(f"panel registration {panel_id} not found")

        selected = [uuid.UUID(str(s)) for s in (panel or [])]
        ids: list[uuid.UUID] = []
        for sid in selected + [benchmark_id]:
            if sid not in ids:
                ids.append(sid)

        # Resolve exactly one current ticker per security id. A missing
        # ticker or an ambiguous (multiple currently-valid) ticker is a
        # hard error: never substitute another security.
        targets: list[CollectionTarget] = []
        for sid in ids:
            ticker_rows = (
                await conn.execute(
                    select(security_identities.c.identifier, security_identities.c.valid_to)
                    .where(
                        security_identities.c.security_id == sid,
                        security_identities.c.identifier_type == "ticker",
                    )
                )
            ).all()
            if not ticker_rows:
                raise CollectionConfigError(
                    f"security {sid} has no ticker identity"
                )
            current = [r.identifier for r in ticker_rows if r.valid_to is None]
            if len(current) != 1:
                raise CollectionConfigError(
                    f"security {sid} has {len(current)} currently-valid tickers; "
                    f"refusing to guess"
                )
            targets.append(CollectionTarget(security_id=sid, ticker=current[0]))

    targets.sort(key=lambda t: t.ticker)
    return targets


async def _last_n_trading_days(
    engine: AsyncEngine, db_now: datetime, n: int
) -> list[date]:
    """The n most recent trading days on or before ``db_now`` (in
    America/New_York), oldest first. Raises when the calendar has not
    been built far enough back — that is a real misconfiguration, not
    something to paper over."""
    local = db_now.astimezone(ET)
    days: list[date] = []
    cursor = local.date()
    while len(days) < n:
        if await is_trading_day(engine, cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


async def missing_trading_days(
    engine: AsyncEngine,
    security_ids: list[uuid.UUID],
    start_date: date,
    end_date: date,
) -> dict[uuid.UUID, list[date]]:
    """For each security, the trading days in ``[start_date, end_date]``
    that have NO price observation (any quality). A present observation
    means the day was fetched at least once; ``zero_volume`` rows still
    count as present (they are a data-quality flag, not a gap).

    This is the "is the target day complete?" check: an HTTP 200, a
    non-empty response or a created=False ingest does NOT prove the day
    is complete — only an actual observation row does.
    """
    from sqlalchemy import func as sa_func

    from youwei_core.db.meta import calendar_days, price_observations
    from youwei_core.data.calendar import VENUE

    result: dict[uuid.UUID, list[date]] = {}
    async with engine.begin() as conn:
        # Trading days inside the window.
        trading = (
            await conn.execute(
                select(calendar_days.c.date).where(
                    calendar_days.c.venue == VENUE,
                    calendar_days.c.is_trading.is_(True),
                    calendar_days.c.date >= start_date,
                    calendar_days.c.date <= end_date,
                )
            )
        ).scalars().all()
        if not trading:
            return result
        for sid in security_ids:
            have = set(
                (
                    await conn.execute(
                        select(sa_func.distinct(price_observations.c.trade_date)).where(
                            price_observations.c.security_id == sid,
                            price_observations.c.trade_date >= start_date,
                            price_observations.c.trade_date <= end_date,
                        )
                    )
                ).scalars().all()
            )
            missing = [d for d in trading if d not in have]
            if missing:
                result[sid] = missing
    return result


def observation_slot(db_now: datetime) -> str:
    """Compute the current collection observation slot in America/New_York.

    Active collection windows (local wall clock):

    - 17:30 ET through midnight: the first collection plus follow-ups.
    - 00:00 through 05:30 ET: follow-up observations for the previous
      trading day's collection window (carries past midnight).
    - 05:30 through 17:29 ET: no active slot (returns "").

    The slot string is the CURRENT 30-minute bucket on the LOCAL date
    (e.g. ``2026-10-11T02:00`` for a follow-up that ran past midnight).
    The caller maps a slot to a trading-day window; a follow-up slot may
    belong to the previous calendar day's collection.
    """
    local = db_now.astimezone(ET)
    t = local.time()
    if FIRST_COLLECTION_TIME_ET <= t or t <= FOLLOWUP_UNTIL_ET:
        minutes = t.hour * 60 + t.minute
        bucket = minutes // FOLLOWUP_INTERVAL_MINUTES * FOLLOWUP_INTERVAL_MINUTES
        return f"{local.date().isoformat()}T{bucket // 60:02d}:{bucket % 60:02d}"
    return ""


async def collect_tick(
    engine: AsyncEngine,
    client: TiingoClient,
    *,
    release_id: str,
    tenant_id: uuid.UUID,
    db_now: datetime | None = None,
) -> dict:
    """One idempotent collection-scheduling pass.

    Resolves the fixed collection set, computes the current window and
    observation slot, and submits one ``data.tiingo_daily`` job per
    target (idempotency key binds tenant + config version + source +
    security_id + date range + slot). Returns a summary; individual
    submission errors are recorded, not raised.
    """
    summary: dict = {
        "targets": 0,
        "submitted": [],
        "idempotent": [],
        "complete": [],
        "gaps": {},
        "errors": [],
    }
    if db_now is not None:
        now = db_now
    else:
        from sqlalchemy import func as sa_func

        async with engine.begin() as conn:
            now = (await conn.execute(select(sa_func.now()))).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    targets = await resolve_collection_targets(engine, release_id)
    summary["targets"] = len(targets)

    slot = observation_slot(now)
    if not slot:
        summary["errors"].append({"phase": "slot", "error": "outside collection window"})
        return summary

    days = await _last_n_trading_days(engine, now, ROLLING_TRADING_DAYS)
    if not days:
        summary["errors"].append({"phase": "window", "error": "no trading days resolved"})
        return summary
    start_date = days[0]
    end_date = days[-1]
    window = f"{start_date.isoformat()}/{end_date.isoformat()}"

    # A day is only complete when an actual observation row exists; an
    # HTTP 200 / non-empty response / created=False does not prove it.
    # Skip targets whose window is already complete; submit the rest.
    missing = await missing_trading_days(
        engine, [t.security_id for t in targets], start_date, end_date
    )

    for target in targets:
        gaps = missing.get(target.security_id)
        if not gaps:
            summary["complete"].append(target.ticker)
            continue
        summary["gaps"][target.ticker] = [d.isoformat() for d in gaps]
        idempotency_key = (
            f"{tenant_id}:{COLLECT_CONFIG_VERSION}:{SOURCE_SLUG}:"
            f"{target.security_id}:{window}:{slot}"
        )
        try:
            result = await submit_run(
                engine,
                tenant_id,
                RunSubmission(
                    kind=COLLECT_JOB_KIND,
                    jobs=[
                        JobSubmission(
                            kind=COLLECT_JOB_KIND,
                            payload={
                                "ticker": target.ticker,
                                "security_id": str(target.security_id),
                                "start_date": start_date.isoformat(),
                                "end_date": end_date.isoformat(),
                            },
                        )
                    ],
                ),
                idempotency_key=idempotency_key,
            )
            if result.created:
                summary["submitted"].append(target.ticker)
            else:
                summary["idempotent"].append(target.ticker)
        except Exception as exc:  # noqa: BLE001 — one target must not stop the tick
            summary["errors"].append(
                {"ticker": target.ticker, "error": str(exc)[:300]}
            )

    return summary
