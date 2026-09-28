"""Panel construction (S06, campaign-policy §2): turn vendor
constituent members into the normalized sampling frame and draw the
fixed 20-security panel with the registered sampler.

The frame normalizes to objects holding ONLY security_id and
sector_code, sorted by security_id; frame_hash covers exactly those
bytes (quant.sampling.normalize_frame). A member without a sector
code stops the draw — never silently dropped.

Ticker mapping: the vendor's ticker is resolved against the securities
master (permanent ids), creating the security when a member is new.
Cross-vendor ticker forms are normalized to the price vendor's
convention (EODHD 'BRK-B' -> 'BRK.B', the form Tiingo uses) so the
same security_id serves price data later.

The panel manifest records the sampler facts (version, seed, quotas,
frame/selection hashes) plus the provenance the data layer owns: the
frame as-of, the source version/mapping and the raw-object reference
it was built from. The stratification label (gics_sector vs a declared
vendor taxonomy) is a protocol decision the caller pins explicitly.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncEngine

from quant.sampling import (
    SAMPLER_VERSION,
    SamplingResult,
    normalize_frame,
    select_sample,
)
from youwei_core.data.securities import IdentitySpec, create_security, resolve_identifier

# the vendor ticker form for dual-class shares differs from the price
# vendor's: EODHD 'BRK-B' vs Tiingo 'BRK.B'. Normalize to the dot form
# so one security_id serves both.
_EODHD_TICKER_MAP = {"-": "."}


def canonical_ticker(code: str) -> str:
    """Normalize a vendor ticker to the price-vendor convention."""
    ticker = str(code).upper()
    for src, dst in _EODHD_TICKER_MAP.items():
        ticker = ticker.replace(src, dst)
    return ticker


@dataclass
class MemberFrame:
    rows: list  # normalized [{security_id, sector_code}] sorted by id
    frame_sha256: str
    mapping: dict  # ticker -> security_id (provenance)
    sector_counts: dict  # sector_code -> N_s


async def build_member_frame(
    engine: AsyncEngine,
    components: list,
    *,
    frame_as_of: date,
) -> MemberFrame:
    """Map vendor members to permanent security ids and normalize the
    frame. A member whose sector is missing stops the draw (the vendor
    parse already guarantees a sector, so this is a defensive check)."""
    if not components:
        raise ValueError("no constituent members to build a frame from")

    frame_rows = []
    mapping: dict[str, str] = {}
    for member in components:
        ticker = canonical_ticker(member["ticker"])
        sector = member.get("sector")
        if not sector or not str(sector).strip():
            raise ValueError(
                f"member {ticker!r} has no sector code: the draw stops here, "
                "members are never silently dropped"
            )
        security_id = await resolve_identifier(engine, "ticker", ticker, frame_as_of)
        if security_id is None:
            security_id = await create_security(
                engine,
                asset_class="equity",
                name=member.get("name"),
                identities=[IdentitySpec("ticker", ticker, date(1990, 1, 1))],
            )
        mapping[ticker] = str(security_id)
        frame_rows.append(
            {"security_id": str(security_id), "sector_code": str(sector)}
        )

    frame = normalize_frame(frame_rows)
    return MemberFrame(
        rows=frame.members,
        frame_sha256=frame.frame_sha256,
        mapping=mapping,
        sector_counts=dict(frame.sector_sizes),
    )


def draw_panel(frame: MemberFrame, *, seed: str, sample_size: int = 20) -> SamplingResult:
    """Draw the panel from a built frame (thin wrapper around the
    registered sampler, kept here so panel construction stays in one
    place)."""
    return select_sample(frame.rows, seed=seed, sample_size=sample_size)


def build_panel_manifest(
    frame: MemberFrame,
    sample: SamplingResult,
    *,
    index: str,
    frame_as_of: date,
    raw_object_id: uuid.UUID,
    source_version: str,
    stratification_level: str,
) -> dict:
    """The panel manifest the campaign registers: the sampler facts
    plus provenance. stratification_level is a protocol decision the
    caller pins (gics_sector vs a declared vendor taxonomy)."""
    manifest = sample.manifest()
    manifest.update(
        {
            "stratification_level": stratification_level,
            "frame_as_of": frame_as_of.isoformat(),
            "index": index,
            "source": {
                "vendor": "EODHD",
                "product": "spglobal-constituents",
                "source_version": source_version,
                "raw_object_id": str(raw_object_id),
                "sector_source": "eodhd:spglobal:Sector",
            },
            "ticker_mapping": {
                ticker: security_id for ticker, security_id in sorted(frame.mapping.items())
            },
        }
    )
    return manifest
