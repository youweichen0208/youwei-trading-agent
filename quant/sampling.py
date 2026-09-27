"""Sector-stratified fixed-seed sampling (campaign-policy §2.1).

The registered algorithm (sampling-json-v1) draws the fixed research
panel from the sampling frame reproducibly:

1. every frame member must carry a PIT sector code — a member without
   one STOPS the draw; nothing is silently dropped or defaulted
2. requirements: frame size M >= sample size N, sector count K <= N
3. every sector gets one seat first; the remaining N-K seats are
   distributed by (N_s - 1)/(M - K): integer parts first, then the
   leftover seats by remainder descending, ties broken by sector code
   ascending. M = K = N is the degenerate one-seat-per-sector case
   with no fraction denominator at all
4. within a sector, members are ordered by
   SHA256(UTF8(compact_JSON([sampler_version, seed, frame_hash,
   sector_code, security_id]))) ascending, ties by security_id, and
   the seats are taken from the top

Serialization (compact JSON): fixed array order, object keys
lexicographic, no extra whitespace, UTF-8, no BOM or trailing
newline. The frame normalizes to objects holding ONLY security_id and
sector_code sorted by security_id; frame_hash covers exactly those
bytes. The selected list is the security_id JSON array ascending
under the same rules.

This module is pure computation with no I/O. Source versions and the
frame's provenance mapping live with the data layer that builds the
frame; the draw manifest records the algorithm facts only.
"""

import hashlib
import json
from dataclasses import dataclass, field

SAMPLER_VERSION = "sector-stratified-hash-v1"
DEFAULT_SEED = "20260927"
DEFAULT_SAMPLE_SIZE = 20


class SamplingError(Exception):
    pass


def compact_json(obj) -> str:
    """The registered serialization: sorted object keys, no extra
    whitespace, UTF-8 (never escaped ASCII), no BOM/newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- frame normalization ---------------------------------------------------------


@dataclass(frozen=True)
class SamplingFrame:
    members: list  # [{"security_id", "sector_code"}] sorted by security_id
    frame_sha256: str
    sector_sizes: dict = field(default_factory=dict)  # sector_code -> N_s


def normalize_frame(members: list) -> SamplingFrame:
    """Validate and normalize: only the two protocol fields survive,
    sorted by security_id. The hash covers exactly these bytes, so any
    input row order selects identically."""
    if not isinstance(members, list) or not members:
        raise SamplingError("the sampling frame must be a non-empty list")
    seen = set()
    rows = []
    for m in members:
        if not isinstance(m, dict):
            raise SamplingError("every frame member must be an object")
        security_id = m.get("security_id")
        sector_code = m.get("sector_code")
        if not isinstance(security_id, str) or not security_id.strip():
            raise SamplingError("every member needs a security_id")
        if not isinstance(sector_code, str) or not sector_code.strip():
            raise SamplingError(
                f"member {security_id!r} has no PIT sector code: the draw "
                "stops here — members are never silently dropped or defaulted"
            )
        if security_id in seen:
            raise SamplingError(f"duplicate security_id {security_id!r} in the frame")
        seen.add(security_id)
        rows.append({"security_id": security_id, "sector_code": sector_code})
    rows.sort(key=lambda r: r["security_id"])
    frame_bytes = compact_json(rows).encode("utf-8")
    sector_sizes: dict[str, int] = {}
    for r in rows:
        sector_sizes[r["sector_code"]] = sector_sizes.get(r["sector_code"], 0) + 1
    return SamplingFrame(
        members=rows,
        frame_sha256=hashlib.sha256(frame_bytes).hexdigest(),
        sector_sizes=sector_sizes,
    )


# --- seat allocation ----------------------------------------------------------------


def allocate_seats(frame: SamplingFrame, sample_size: int = DEFAULT_SAMPLE_SIZE) -> dict:
    """The registered allocation: one seat per sector, then the
    remaining seats by (N_s - 1)/(M - K) — floors first, leftover to
    the largest remainders, ties by sector code ascending."""
    m = len(frame.members)
    k = len(frame.sector_sizes)
    if m < sample_size:
        raise SamplingError(
            f"frame has {m} members, at least {sample_size} required"
        )
    if k > sample_size:
        raise SamplingError(
            f"frame has {k} sectors, at most {sample_size} possible "
            "(every sector must get a seat)"
        )
    sectors = sorted(frame.sector_sizes)  # ties resolve by this order
    seats = {s: 1 for s in sectors}
    remaining = sample_size - k
    if remaining == 0:
        return seats  # M = K = N: no fraction denominator at all

    denominator = m - k
    exact = {s: remaining * (frame.sector_sizes[s] - 1) / denominator for s in sectors}
    floors = {s: int(exact[s]) for s in sectors}
    for s in sectors:
        seats[s] += floors[s]
    leftover = remaining - sum(floors.values())
    by_remainder = sorted(
        sectors, key=lambda s: (-exact[s] + floors[s], s)
    )  # remainder desc, sector code asc
    for s in by_remainder[:leftover]:
        seats[s] += 1

    if sum(seats.values()) != sample_size:
        raise SamplingError("allocation does not fill the sample size exactly")
    for s in sectors:
        if seats[s] > frame.sector_sizes[s]:
            raise SamplingError(
                f"allocation gives sector {s!r} {seats[s]} seats but it only "
                f"has {frame.sector_sizes[s]} members"
            )
    return seats


# --- selection ---------------------------------------------------------------------


def selection_hash(
    sampler_version: str,
    seed: str,
    frame_sha256: str,
    sector_code: str,
    security_id: str,
) -> str:
    payload = compact_json(
        [sampler_version, seed, frame_sha256, sector_code, security_id]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class SamplingResult:
    selected: list  # security_ids ascending
    quotas: dict  # sector_code -> seats
    frame_sha256: str
    selected_list_sha256: str
    sampler_version: str
    seed: str
    sample_size: int

    def manifest(self) -> dict:
        """The draw facts the campaign's panel manifest records (source
        versions and the frame's provenance mapping are added by the
        data layer that built the frame)."""
        return {
            "sampler_version": self.sampler_version,
            "seed": self.seed,
            "sample_size": self.sample_size,
            "quotas": self.quotas,
            "frame_sha256": self.frame_sha256,
            "selected_list_sha256": self.selected_list_sha256,
            "selected": self.selected,
        }


def select_sample(
    members: list,
    *,
    seed: str = DEFAULT_SEED,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    sampler_version: str = SAMPLER_VERSION,
) -> SamplingResult:
    """Draw the fixed panel from the frame. Deterministic: the same
    frame and parameters always produce the same selection."""
    frame = normalize_frame(members)
    seats = allocate_seats(frame, sample_size)

    by_sector: dict[str, list[str]] = {s: [] for s in frame.sector_sizes}
    for row in frame.members:
        by_sector[row["sector_code"]].append(row["security_id"])

    selected: list[str] = []
    for sector in sorted(by_sector):
        candidates = sorted(
            by_sector[sector],
            key=lambda sec_id: (
                selection_hash(
                    sampler_version, seed, frame.frame_sha256, sector, sec_id
                ),
                sec_id,
            ),
        )
        selected.extend(candidates[: seats[sector]])

    selected.sort()
    selected_list_sha256 = hashlib.sha256(compact_json(selected).encode("utf-8")).hexdigest()
    return SamplingResult(
        selected=selected,
        quotas=seats,
        frame_sha256=frame.frame_sha256,
        selected_list_sha256=selected_list_sha256,
        sampler_version=sampler_version,
        seed=seed,
        sample_size=sample_size,
    )
