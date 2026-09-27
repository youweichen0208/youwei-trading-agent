"""API key authentication: bearer credentials mapping principals to
tenants. Raw tokens are shown exactly once (creation response); the
database stores only sha256 hashes. The admin bootstrap key lives in
settings (operator-configured) and never touches this table."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import func

from youwei_core.db.meta import api_keys, tenants

TOKEN_PREFIX = "ywa_"


@dataclass
class Principal:
    tenant_id: uuid.UUID | None  # None for the admin bootstrap principal
    role: str  # "tenant" | "admin"
    key_id: uuid.UUID | None


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


async def create_tenant(
    engine: AsyncEngine, slug: str, *, tenant_id: uuid.UUID | None = None
) -> uuid.UUID:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    tenant_id = tenant_id or uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(tenants)
            .values(id=tenant_id, slug=slug)
            .on_conflict_do_nothing(index_elements=[tenants.c.id])
        )
    return tenant_id


async def create_api_key(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    name: str,
    *,
    role: str = "tenant",
    raw_token: str | None = None,
) -> tuple[uuid.UUID, str]:
    """Create an API key; returns (key_id, raw_token). The raw token is
    never stored and cannot be recovered later."""
    raw = raw_token or generate_token()
    key_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            api_keys.insert().values(
                id=key_id,
                tenant_id=tenant_id,
                name=name,
                role=role,
                token_hash=_hash_token(raw),
                token_prefix=raw[:12],
            )
        )
    return key_id, raw


async def revoke_api_key(engine: AsyncEngine, key_id: uuid.UUID) -> bool:
    async with engine.begin() as conn:
        result = await conn.execute(
            update(api_keys)
            .where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        return result.rowcount == 1


async def authenticate(engine: AsyncEngine, raw_token: str) -> Principal | None:
    """Resolve a bearer token to a principal. None when unknown or
    revoked."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(api_keys)
                .where(
                    api_keys.c.token_hash == _hash_token(raw_token),
                    api_keys.c.revoked_at.is_(None),
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return Principal(tenant_id=row.tenant_id, role=row.role, key_id=row.id)


def utcnow() -> datetime:
    return datetime.now(UTC)
