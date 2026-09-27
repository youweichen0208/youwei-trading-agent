"""Point-in-time daily bar queries.

As-of semantics (architecture section 4): for each (security,
trade_date) return the observation from the latest raw object whose
usable_at <= as_of. Backfills and vendor corrections create new
versions; they never change what an earlier as_of would have returned.

Callers must pass an explicit as_of and mode:
- 'forward': a controller's decision cutoff; must not be in the future
- 'historical_source': reconstruction of "what the market could have
  seen"; explicitly labeled and never presentable as a record of what
  the system actually ran at the time
"""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import price_observations, raw_objects

MODES = ("forward", "historical_source")


class InvalidQuery(Exception):
    pass


async def daily_bars_asof(
    engine: AsyncEngine,
    security_ids: list,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
    mode: str,
) -> list[dict]:
    """Return the PIT-latest bar per (security, trade_date) with full
    provenance (raw object id, usable_at, source_available_at,
    ingested_at). Rows that were not yet usable at as_of are absent —
    never filled with later knowledge."""
    if mode not in MODES:
        raise InvalidQuery(f"mode must be one of {MODES}, got {mode!r}")
    if start_date > end_date:
        raise InvalidQuery("start_date after end_date")
    if not security_ids:
        return []

    async with engine.begin() as conn:
        if mode == "forward":
            db_now = (await conn.execute(select(func.now()))).scalar_one()
            if as_of > db_now:
                raise InvalidQuery(
                    f"forward as_of {as_of} is in the future (db now {db_now})"
                )

        rows = (
            (
                await conn.execute(
                    select(
                        price_observations.c.security_id,
                        price_observations.c.trade_date,
                        price_observations.c.open,
                        price_observations.c.high,
                        price_observations.c.low,
                        price_observations.c.close,
                        price_observations.c.volume,
                        price_observations.c.adj_close,
                        price_observations.c.div_cash,
                        price_observations.c.split_factor,
                        price_observations.c.quality,
                        raw_objects.c.id.label("raw_object_id"),
                        raw_objects.c.usable_at,
                        raw_objects.c.source_available_at,
                        raw_objects.c.ingested_at,
                        raw_objects.c.source_available_basis,
                    )
                    .join(
                        raw_objects,
                        raw_objects.c.id == price_observations.c.raw_object_id,
                    )
                    .where(
                        price_observations.c.security_id.in_(security_ids),
                        price_observations.c.trade_date >= start_date,
                        price_observations.c.trade_date <= end_date,
                        raw_objects.c.usable_at <= as_of,
                    )
                    # DISTINCT ON (PG): latest usable version per
                    # (security, trade_date); ORDER BY must lead with
                    # the DISTINCT ON expressions
                    .prefix_with(
                        "DISTINCT ON (security_id, trade_date)",
                        dialect="postgresql",
                    )
                    .order_by(
                        price_observations.c.security_id,
                        price_observations.c.trade_date,
                        raw_objects.c.usable_at.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )

    return [
        {
            "security_id": str(r.security_id),
            "trade_date": r.trade_date.isoformat(),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": r.volume,
            "adj_close": float(r.adj_close) if r.adj_close is not None else None,
            "div_cash": float(r.div_cash),
            "split_factor": float(r.split_factor),
            "quality": r.quality,
            "provenance": {
                "raw_object_id": str(r.raw_object_id),
                "usable_at": r.usable_at.isoformat(),
                "source_available_at": (
                    r.source_available_at.isoformat()
                    if r.source_available_at is not None
                    else None
                ),
                "ingested_at": r.ingested_at.isoformat(),
                "source_available_basis": r.source_available_basis,
            },
        }
        for r in rows
    ]
