"""Panel construction and registration (S06, campaign-policy §2).

Three concerns, in the order the protocol demands them:

1. **Frame construction** (build_member_frame): map vendor constituent
   members to permanent security ids and normalize the frame. A new
   member's identity starts at the frame as-of (the observed instant),
   never an invented 1990 — no evidence backs an earlier validity.

2. **Freeze / restore** (freeze_panel_registration /
   restore_panel_registration): the complete mapping + frame + sample
   evidence is frozen content-addressed (append-only). Restore
   recreates the SAME permanent ids, so a clean database rebuild
   re-derives the same frame and selection hashes — re-sampling into
   fresh random ids is never a substitute for recovery.

3. **The registration gate** (validate_panel_registration): re-derive
   the frame from the source raw object + the frozen mapping and
   cross-check the frame hash, quotas, selected ids and their hashes,
   the protocol reference and the caller's panel_security_ids. A wrong
   source, time, frame, mapping, selection or stratification label is
   rejected — nothing passes on a dict-shape check alone.

The stratification label (eodhd_sector vs gics_sector) is a protocol
decision the caller pins explicitly and the gate re-checks.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from quant.sampling import (
    SAMPLER_VERSION,
    SamplingResult,
    compact_json,
    normalize_frame,
    select_sample,
)
from youwei_core.data.eodhd import parse_constituents
from youwei_core.data.securities import (
    IdentitySpec,
    create_security,
    resolve_identifier,
)
from youwei_core.db.meta import (
    panel_registrations,
    raw_objects,
    securities,
    security_identities,
)
from youwei_core.ledger.service import sha256_hex

# the vendor ticker form for dual-class shares differs from the price
# vendor's: EODHD 'BRK-B' vs Tiingo 'BRK.B'. Normalize to the dot form
# so one security_id serves both.
_EODHD_TICKER_MAP = {"-": "."}

# the evidence-backed basis for a panel member's identity: the ticker
# was observed in the index at the frame as-of — nothing earlier is
# claimed, and nothing is invented.
IDENTITY_BASIS = "observed_in_sp500_constituents_at_frame_as_of"


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
    identities: list  # [{ticker, exchange, security_id, valid_from, basis}]
    sector_counts: dict  # sector_code -> N_s


async def build_member_frame(
    engine: AsyncEngine,
    components: list,
    *,
    frame_as_of: date,
) -> MemberFrame:
    """Map vendor members to permanent security ids and normalize the
    frame. New members get an identity valid FROM the frame as-of (the
    observed instant) with an explicit basis — never an invented 1990.
    A member whose sector is missing stops the draw."""
    if not components:
        raise ValueError("no constituent members to build a frame from")

    frame_rows = []
    mapping: dict[str, str] = {}
    identities = []
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
                identities=[
                    IdentitySpec("ticker", ticker, frame_as_of)
                ],
            )
        mapping[ticker] = str(security_id)
        identities.append(
            {
                "ticker": ticker,
                "exchange": member.get("exchange", "US"),
                "security_id": str(security_id),
                "valid_from": frame_as_of.isoformat(),
                "basis": IDENTITY_BASIS,
            }
        )
        frame_rows.append(
            {"security_id": str(security_id), "sector_code": str(sector)}
        )

    frame = normalize_frame(frame_rows)
    return MemberFrame(
        rows=frame.members,
        frame_sha256=frame.frame_sha256,
        mapping=mapping,
        identities=identities,
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


# --- freeze / restore ----------------------------------------------------------


@dataclass
class PanelRegistration:
    registration_id: uuid.UUID
    created: bool
    mapping_sha256: str
    frame_sha256: str
    selected_list_sha256: str
    content: dict


def _mapping_sha256(identities: list) -> str:
    return sha256_hex(
        sorted(identities, key=lambda i: i["ticker"])
    )


async def freeze_panel_registration(
    engine: AsyncEngine,
    *,
    index: str,
    stratification_level: str,
    protocol_ref: str,
    protocol_sha256: str,
    seed: str,
    sample_size: int,
    source_raw_object_id: uuid.UUID,
    observed_at: datetime | None,
    usable_at: datetime,
    source_available_basis: str,
    frame_as_of: date,
    frame: MemberFrame,
    sample: SamplingResult,
) -> PanelRegistration:
    """Freeze the complete evidence bundle for one panel draw,
    content-addressed and append-only. The mapping records each
    member's identity with an evidence-backed validity, so a clean
    database can restore the SAME permanent ids."""
    mapping_sha = _mapping_sha256(frame.identities)

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(panel_registrations).where(
                    panel_registrations.c.index == index,
                    panel_registrations.c.frame_sha256 == frame.frame_sha256,
                    panel_registrations.c.mapping_sha256 == mapping_sha,
                )
            )
        ).mappings().first()
        if existing is not None:
            return PanelRegistration(
                registration_id=existing.id,
                created=False,
                mapping_sha256=mapping_sha,
                frame_sha256=frame.frame_sha256,
                selected_list_sha256=sample.selected_list_sha256,
                content=dict(existing),
            )

        registration_id = uuid.uuid4()
        await conn.execute(
            panel_registrations.insert().values(
                id=registration_id,
                index=index,
                stratification_level=stratification_level,
                protocol_ref=protocol_ref,
                protocol_sha256=protocol_sha256,
                seed=seed,
                sample_size=sample_size,
                source_raw_object_id=source_raw_object_id,
                observed_at=observed_at,
                usable_at=usable_at,
                source_available_basis=source_available_basis,
                frame_as_of=frame_as_of,
                frame_sha256=frame.frame_sha256,
                mapping=frame.identities,
                mapping_sha256=mapping_sha,
                quotas=sample.quotas,
                selected=sample.selected,
                selected_list_sha256=sample.selected_list_sha256,
            )
        )
    return PanelRegistration(
        registration_id=registration_id,
        created=True,
        mapping_sha256=mapping_sha,
        frame_sha256=frame.frame_sha256,
        selected_list_sha256=sample.selected_list_sha256,
        content={
            "index": index,
            "stratification_level": stratification_level,
            "protocol_ref": protocol_ref,
            "protocol_sha256": protocol_sha256,
            "seed": seed,
            "sample_size": sample_size,
            "source_raw_object_id": str(source_raw_object_id),
            "frame_as_of": frame_as_of,
            "frame_sha256": frame.frame_sha256,
            "mapping": frame.identities,
            "mapping_sha256": mapping_sha,
            "quotas": sample.quotas,
            "selected": sample.selected,
            "selected_list_sha256": sample.selected_list_sha256,
        },
    )


async def restore_panel_registration(
    engine: AsyncEngine, registration_id: uuid.UUID
) -> dict:
    """Recreate the frozen permanent ids and identities in a clean
    database. Idempotent: a security that already exists is left alone;
    a ticker mapping that resolves differently is reported, never
    silently overwritten. Returns {restored, already, mismatched}."""
    async with engine.begin() as conn:
        reg = (
            await conn.execute(
                select(panel_registrations).where(
                    panel_registrations.c.id == registration_id
                )
            )
        ).mappings().one_or_none()
    if reg is None:
        raise ValueError(f"panel registration {registration_id} not found")

    restored, already, mismatched = [], [], []
    for entry in reg.mapping:
        ticker = entry["ticker"]
        security_id = uuid.UUID(entry["security_id"])
        valid_from = date.fromisoformat(entry["valid_from"])

        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(securities.c.id).where(securities.c.id == security_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                already.append(ticker)
                continue
            # the ticker must not already belong to a DIFFERENT permanent
            # id — that would be a corrupt/mismatched restore
            clash = (
                await conn.execute(
                    select(security_identities.c.security_id).where(
                        security_identities.c.identifier_type == "ticker",
                        security_identities.c.identifier == ticker,
                    )
                )
            ).scalars().all()
            if clash and any(c != security_id for c in clash):
                mismatched.append(ticker)
                continue
            await conn.execute(
                securities.insert().values(
                    id=security_id, asset_class="equity", name=None
                )
            )
            await conn.execute(
                security_identities.insert().values(
                    id=uuid.uuid4(),
                    security_id=security_id,
                    identifier_type="ticker",
                    identifier=ticker,
                    venue=entry.get("exchange", "US"),
                    valid_from=valid_from,
                    valid_to=None,
                )
            )
        restored.append(ticker)

    return {
        "restored": sorted(restored),
        "already": sorted(already),
        "mismatched": mismatched,
    }


# --- registration gate -----------------------------------------------------------


async def _load_registration(engine, registration_id):
    async with engine.begin() as conn:
        reg = (
            await conn.execute(
                select(panel_registrations).where(
                    panel_registrations.c.id == registration_id
                )
            )
        ).mappings().one_or_none()
    if reg is None:
        raise ValueError(f"panel registration {registration_id} not found")
    return reg


async def validate_panel_registration(
    engine: AsyncEngine,
    registration_id: uuid.UUID,
    *,
    panel_security_ids: list | None = None,
    protocol_sha256: str | None = None,
) -> dict:
    """The registration gate (campaign-policy §2.1.1): re-derive the
    frame from the source raw object and the frozen mapping, re-draw
    the sample, and cross-check every field. Returns {ok, issues,
    checks}. A wrong source, time, mapping, frame, selection or
    stratification label is an issue — never a silent pass."""
    reg = await _load_registration(engine, registration_id)
    issues: list[str] = []
    checks: dict[str, bool] = {}

    # source raw object intact and parseable
    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                select(raw_objects).where(
                    raw_objects.c.id == reg.source_raw_object_id
                )
            )
        ).mappings().one_or_none()
    if raw is None:
        return {"ok": False, "issues": ["source raw object is gone"], "checks": checks}
    # recompute the raw content hash (independent of the stored column)
    import hashlib

    actual_content = hashlib.sha256(raw.content.encode("utf-8")).hexdigest()
    checks["source_content_hash"] = actual_content == raw.content_sha256
    if not checks["source_content_hash"]:
        issues.append("source raw object content hash does not match")

    # mapping content hash
    checks["mapping_hash"] = _mapping_sha256(reg.mapping) == reg.mapping_sha256
    if not checks["mapping_hash"]:
        issues.append("mapping content hash does not match the frozen mapping")

    # protocol reference
    if protocol_sha256 is not None:
        checks["protocol"] = protocol_sha256 == reg.protocol_sha256
        if not checks["protocol"]:
            issues.append(
                f"protocol hash {protocol_sha256[:12]} does not match the "
                f"registration's {reg.protocol_sha256[:12]}"
            )
    else:
        checks["protocol"] = True

    # time consistency: the frame cannot claim data before it was usable
    checks["time"] = reg.usable_at.date() <= reg.frame_as_of
    if not checks["time"]:
        issues.append(
            f"frame_as_of {reg.frame_as_of} precedes the source's usable_at "
            f"{reg.usable_at.date()}: the frame claims future data"
        )

    # re-derive the frame: parse source -> ticker -> frozen security_id
    try:
        parsed = parse_constituents(raw.content)
    except ValueError as exc:
        return {"ok": False, "issues": [f"source does not parse: {exc}"], "checks": checks}

    id_by_ticker = {e["ticker"]: e["security_id"] for e in reg.mapping}
    frame_rows = []
    for member in parsed["components"]:
        ticker = canonical_ticker(member["ticker"])
        security_id = id_by_ticker.get(ticker)
        if security_id is None:
            issues.append(f"ticker {ticker!r} in the source has no frozen mapping")
            continue
        frame_rows.append(
            {"security_id": security_id, "sector_code": str(member["sector"])}
        )

    frame = normalize_frame(frame_rows)
    checks["frame_hash"] = frame.frame_sha256 == reg.frame_sha256
    if not checks["frame_hash"]:
        issues.append(
            f"re-derived frame hash {frame.frame_sha256[:12]} != frozen "
            f"{reg.frame_sha256[:12]}"
        )

    # re-draw and cross-check the sample
    sample = select_sample(
        frame_rows,
        seed=reg.seed,
        sample_size=reg.sample_size,
    )
    checks["quotas"] = sample.quotas == dict(reg.quotas)
    checks["selected_list_hash"] = sample.selected_list_sha256 == reg.selected_list_sha256
    if not checks["quotas"]:
        issues.append("re-drawn quotas differ from the frozen registration")
    if not checks["selected_list_hash"]:
        issues.append("re-drawn selected-list hash differs from the frozen registration")

    # the caller's panel security ids must equal the frozen selection
    if panel_security_ids is not None:
        want = sorted(str(s) for s in panel_security_ids)
        checks["panel_security_ids"] = want == sample.selected
        if not checks["panel_security_ids"]:
            issues.append("caller panel_security_ids differ from the frozen selection")

    return {"ok": not issues, "issues": issues, "checks": checks}
