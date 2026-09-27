"""Async engine factory. Short transactions everywhere: services use
engine.begin() per operation; nothing holds a transaction across awaits
on external systems."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(database_url: str, *, pool_size: int = 2, max_overflow: int = 0) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )
