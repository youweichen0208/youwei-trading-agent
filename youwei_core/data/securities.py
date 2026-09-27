"""Securities master: permanent identities + point-in-time identifier
resolution.

The security id is permanent; tickers/CUSIPs/ISINs are names attached
to a security with validity ranges (a ticker is reusable: the SGEN
phantom-row finding showed vendor endDate cannot decide listing
status, and name reuse is exactly why resolution must be as-of dated).
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import security_identities, securities

IDENTIFIER_TYPES = ("ticker", "perma_ticker", "cusip", "isin", "figi")


class AmbiguousIdentifier(Exception):
    """More than one security carries this identifier at the as-of
    date: the master data needs fixing before queries can be trusted."""


class UnknownSecurity(Exception):
    pass


@dataclass
class IdentitySpec:
    identifier_type: str
    identifier: str
    valid_from: date
    valid_to: date | None = None
    venue: str = "US"


async def create_security(
    engine: AsyncEngine,
    *,
    asset_class: str = "equity",
    name: str | None = None,
    identities: list[IdentitySpec] | None = None,
) -> uuid.UUID:
    if asset_class not in ("equity", "etf"):
        raise ValueError(f"unsupported asset_class {asset_class!r}")
    security_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            securities.insert().values(
                id=security_id, asset_class=asset_class, name=name
            )
        )
        for ident in identities or []:
            await conn.execute(
                security_identities.insert().values(
                    id=uuid.uuid4(),
                    security_id=security_id,
                    identifier_type=ident.identifier_type,
                    identifier=ident.identifier,
                    venue=ident.venue,
                    valid_from=ident.valid_from,
                    valid_to=ident.valid_to,
                )
            )
    return security_id


async def add_identity(
    engine: AsyncEngine, security_id: uuid.UUID, ident: IdentitySpec
) -> uuid.UUID:
    if ident.identifier_type not in IDENTIFIER_TYPES:
        raise ValueError(f"unsupported identifier_type {ident.identifier_type!r}")
    identity_id = uuid.uuid4()
    async with engine.begin() as conn:
        exists = (
            await conn.execute(
                select(func.count()).where(securities.c.id == security_id)
            )
        ).scalar_one()
        if not exists:
            raise UnknownSecurity(str(security_id))
        await conn.execute(
            security_identities.insert().values(
                id=identity_id,
                security_id=security_id,
                identifier_type=ident.identifier_type,
                identifier=ident.identifier,
                venue=ident.venue,
                valid_from=ident.valid_from,
                valid_to=ident.valid_to,
            )
        )
    return identity_id


async def resolve_identifier(
    engine: AsyncEngine,
    identifier_type: str,
    identifier: str,
    as_of: date,
    *,
    venue: str = "US",
) -> uuid.UUID | None:
    """Resolve an identifier to the security that carried it at
    as_of. None when no security held it then; AmbiguousIdentifier
    when several did (master data defect, not a query error)."""
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                select(security_identities.c.security_id).where(
                    security_identities.c.identifier_type == identifier_type,
                    func.upper(security_identities.c.identifier)
                    == identifier.upper(),
                    security_identities.c.venue == venue,
                    security_identities.c.valid_from <= as_of,
                    func.coalesce(
                        security_identities.c.valid_to, date.max
                    )
                    >= as_of,
                )
            )
        ).scalars().all()
    distinct = set(rows)
    if len(distinct) > 1:
        raise AmbiguousIdentifier(
            f"{identifier_type} {identifier!r} at {as_of} maps to "
            f"{len(distinct)} securities"
        )
    return next(iter(distinct), None)
