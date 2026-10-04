"""Read-only daily data for the personal assistant; no forecast writes."""
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select

from youwei_core.data.pit import InvalidQuery, daily_bars_asof
from youwei_core.data.securities import UnknownSecurity, resolve_identifier
from youwei_core.db.meta import data_sources, raw_objects


async def query_daily_bars(engine, *, ticker: str, start_date: date,
                           end_date: date, as_of: datetime | None):
    async with engine.connect() as conn:
        now = (await conn.execute(select(func.now()))).scalar_one()
    cutoff = as_of or now
    if cutoff.tzinfo is None or cutoff > now:
        raise InvalidQuery('as_of must be timezone-aware and not in the future')
    if start_date > end_date or (end_date - start_date).days > 3660:
        raise InvalidQuery('date range must be ordered and at most 3660 days')
    security_id = await resolve_identifier(engine, 'ticker', ticker, end_date)
    if security_id is None:
        raise UnknownSecurity(ticker)
    bars = await daily_bars_asof(engine, [security_id], start_date, end_date,
                               as_of=cutoff, mode='forward')
    ids = [UUID(b['provenance']['raw_object_id']) for b in bars]
    async with engine.connect() as conn:
        sources = (await conn.execute(
            select(raw_objects.c.id, raw_objects.c.source_id,
                   data_sources.c.vendor_version, data_sources.c.license_tags)
            .join(data_sources, data_sources.c.id == raw_objects.c.source_id)
            .where(raw_objects.c.id.in_(ids))
        )).mappings().all() if ids else []
    by_id = {str(row['id']): row for row in sources}
    for bar in bars:
        row = by_id[bar['provenance']['raw_object_id']]
        bar['provenance'].update({k: row[k] for k in
                                  ('source_id', 'vendor_version', 'license_tags')})
    return dict(ticker=ticker.upper(), security_id=str(security_id),
                identifier_as_of=end_date, start_date=start_date, end_date=end_date,
                as_of=cutoff, queried_at=now, mode='forward', frequency='daily',
                bars=bars, limitations=['Stored daily bars, not real-time quotes.',
                                       'Missing dates are not filled.'])
