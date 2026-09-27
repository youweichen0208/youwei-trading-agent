"""S04: securities master — permanent ids, identifier validity ranges,
point-in-time ticker resolution (ticker reuse is the norm, not the
exception)."""

from datetime import date

import pytest

from youwei_core.data.securities import (
    AmbiguousIdentifier,
    IdentitySpec,
    UnknownSecurity,
    add_identity,
    create_security,
    resolve_identifier,
)


async def test_create_security_with_identities(db_engine):
    sec = await create_security(
        db_engine,
        asset_class="etf",
        name="SPDR S&P 500 ETF Trust",
        identities=[
            IdentitySpec(
                identifier_type="ticker",
                identifier="SPY",
                valid_from=date(1993, 1, 22),
            )
        ],
    )
    assert sec is not None
    resolved = await resolve_identifier(db_engine, "ticker", "SPY", date(2026, 9, 25))
    assert resolved == sec


async def test_resolve_ticker_is_as_of_dated_reuse(db_engine):
    """SGEN-style reuse: one ticker, two securities, disjoint validity."""
    seagen = await create_security(
        db_engine,
        name="Seagen Inc",
        identities=[
            IdentitySpec(
                identifier_type="ticker",
                identifier="SGEN",
                valid_from=date(2001, 3, 9),
                valid_to=date(2023, 12, 14),
            )
        ],
    )
    successor = await create_security(
        db_engine,
        name="Successor Co",
        identities=[
            IdentitySpec(
                identifier_type="ticker",
                identifier="SGEN",
                valid_from=date(2024, 6, 3),
            )
        ],
    )

    assert await resolve_identifier(db_engine, "ticker", "SGEN", date(2023, 6, 30)) == seagen
    assert await resolve_identifier(db_engine, "ticker", "SGEN", date(2026, 9, 25)) == successor
    # between the two validity ranges nobody held the name
    assert await resolve_identifier(db_engine, "ticker", "SGEN", date(2024, 1, 15)) is None


async def test_resolve_ticker_case_insensitive(db_engine):
    sec = await create_security(
        db_engine,
        identities=[IdentitySpec("ticker", "AAPL", date(1980, 12, 12))],
    )
    assert await resolve_identifier(db_engine, "ticker", "aapl", date(2026, 9, 25)) == sec


async def test_resolve_unknown_ticker_returns_none(db_engine):
    assert await resolve_identifier(db_engine, "ticker", "ZZZZZZ", date(2026, 9, 25)) is None


async def test_resolve_ambiguous_raises(db_engine):
    """Two securities claiming the same ticker at the same date is a
    master-data defect: fail loudly, never guess."""
    await create_security(
        db_engine, identities=[IdentitySpec("ticker", "DUP", date(2020, 1, 1))]
    )
    await create_security(
        db_engine, identities=[IdentitySpec("ticker", "DUP", date(2021, 1, 1))]
    )
    with pytest.raises(AmbiguousIdentifier):
        await resolve_identifier(db_engine, "ticker", "DUP", date(2026, 9, 25))


async def test_add_identity_validates_type_and_security(db_engine):
    sec = await create_security(db_engine)
    with pytest.raises(ValueError):
        await add_identity(
            db_engine, sec, IdentitySpec("not_a_type", "X", date(2020, 1, 1))
        )
    with pytest.raises(UnknownSecurity):
        await add_identity(
            db_engine,
            "00000000-0000-0000-0000-000000000000",
            IdentitySpec("ticker", "X", date(2020, 1, 1)),
        )
