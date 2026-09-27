"""Outbox publisher: marks pending events as published.

S02a scope: the transactional outbox is the events table itself —
rows are appended in the same transaction as state changes
(published_at NULL). The publisher marks delivery; real fanout
consumers (CN mirror) arrive with S09 and read by seq cursor.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import events


async def publish_pending_events(engine: AsyncEngine, *, batch: int = 100) -> int:
    """Mark the oldest unpublished events published. Returns how many."""
    async with engine.begin() as conn:
        marked = (
            await conn.execute(
                update(events)
                .where(
                    events.c.seq.in_(
                        select(events.c.seq)
                        .where(events.c.published_at.is_(None))
                        .order_by(events.c.seq)
                        .limit(batch)
                    )
                )
                .values(published_at=func.now())
                .returning(events.c.seq)
            )
        ).all()
        return len(marked)
