"""Known answers for the registered sampler (campaign-policy §2.1,
sampling-json-v1) — the K01 synthetic frame.

K01: 11 sectors S01..S11, each holding SEC-xx-1 and SEC-xx-2 (M=22,
K=11); seed "20260927", N=20. One seat per sector first, the
remaining 9 by equal remainders tied to ascending sector code:
S01..S09 get 2 seats, S10/S11 get 1. The last two sectors select
SEC-10-2 and SEC-11-1, and the compact-JSON SHA-256 of the selected
list is the recorded 37421b62… (verified independently at protocol
time; this implementation must reproduce it).

The recorded hash belongs to the synthetic sample only — it can
never be pasted into a real S&P 500 panel manifest.
"""

import random

import pytest

from quant.sampling import (
    SAMPLER_VERSION,
    SamplingError,
    compact_json,
    normalize_frame,
    select_sample,
)

K01_HASH = "37421b62f9a366df2ceddc433c887b9a9eab7d6d37365fa4d85acbf4cc5d5dcb"


def _k01_frame():
    members = []
    for k in range(1, 12):
        sector = f"S{k:02d}"
        members.append({"security_id": f"SEC-{k:02d}-1", "sector_code": sector})
        members.append({"security_id": f"SEC-{k:02d}-2", "sector_code": sector})
    return members


def test_k01_fixed_seed_stratified_sampling():
    result = select_sample(_k01_frame())

    # quotas: every sector one seat, S01..S09 a second
    assert set(result.quotas.values()) == {1, 2}
    assert [result.quotas[f"S{k:02d}"] for k in range(1, 10)] == [2] * 9
    assert result.quotas["S10"] == 1
    assert result.quotas["S11"] == 1

    # the recorded last-two-sector picks
    in_s10 = [s for s in result.selected if s.startswith("SEC-10-")]
    in_s11 = [s for s in result.selected if s.startswith("SEC-11-")]
    assert in_s10 == ["SEC-10-2"]
    assert in_s11 == ["SEC-11-1"]

    # the full selection is pinned by the recorded compact-JSON hash
    assert len(result.selected) == 20
    assert result.selected == sorted(result.selected)
    assert result.selected_list_sha256 == K01_HASH
    assert result.sampler_version == SAMPLER_VERSION
    assert result.seed == "20260927"
    assert result.sample_size == 20


def test_k01_row_order_invariance():
    """Normalized inputs hash and select identically regardless of the
    order rows arrive in (K01: shuffling the frame changes nothing)."""
    base = select_sample(_k01_frame())
    shuffled = _k01_frame()
    random.Random(20260927).shuffle(shuffled)
    again = select_sample(shuffled)
    assert again.frame_sha256 == base.frame_sha256
    assert again.quotas == base.quotas
    assert again.selected == base.selected
    assert again.selected_list_sha256 == base.selected_list_sha256


def test_compact_json_rules():
    assert compact_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'  # keys sorted
    assert compact_json(["x", "y"]) == '["x","y"]'  # array order fixed, no spaces
    assert compact_json({"k": "é"}) == '{"k":"é"}'  # UTF-8, no escaping


# --- allocation arithmetic ------------------------------------------------------


def test_allocation_floor_then_remainder_descending():
    """Distinct remainders: floors first, then leftover seats to the
    largest remainders (hand-computed).

    K=5 sectors of sizes 10, 6, 3, 1, 1 -> M=21, R=15 over M-K=16:
    exact 8.4375, 4.6875, 1.875, 0, 0 -> floors 8, 4, 1, 0, 0 (sum 13);
    remainders .875 (X03), .6875 (X02), .4375 (X01) -> the two largest
    take the 2 leftover seats -> seats 9, 6, 3, 1, 1.
    """
    members = []
    for i, n in enumerate([10, 6, 3, 1, 1], start=1):
        for j in range(1, n + 1):
            members.append(
                {"security_id": f"X-{i:02d}-{j:03d}", "sector_code": f"X{i:02d}"}
            )
    result = select_sample(members)
    assert result.quotas == {
        "X01": 9,
        "X02": 6,
        "X03": 3,
        "X04": 1,
        "X05": 1,
    }
    assert len(result.selected) == 20
    assert sum(result.quotas.values()) == 20


def test_exact_frame_one_seat_per_sector():
    """M=K=20: every sector gets exactly one seat; the zero-remainder
    branch skips the fraction denominator entirely."""
    members = [
        {"security_id": f"ONE-{k:02d}", "sector_code": f"C{k:02d}"}
        for k in range(1, 21)
    ]
    result = select_sample(members)
    assert result.quotas == {f"C{k:02d}": 1 for k in range(1, 21)}
    assert len(result.selected) == 20
    assert result.selected == sorted(result.selected)


# --- rejections (stop registration, never silently drop) ---------------------------


def test_frame_validations():
    with pytest.raises(SamplingError, match="at least 20"):
        select_sample([{"security_id": "A", "sector_code": "S"}])
    with pytest.raises(SamplingError, match="sector"):
        # a member without a PIT sector code stops registration — it is
        # never silently dropped from the frame
        frame = _k01_frame() + [{"security_id": "SEC-99-1", "sector_code": ""}]
        select_sample(frame)
    with pytest.raises(SamplingError, match="duplicate"):
        frame = _k01_frame() + [{"security_id": "SEC-01-1", "sector_code": "S01"}]
        select_sample(frame)
    with pytest.raises(SamplingError, match="at most 20"):
        # 21 sectors: K > 20 cannot give every sector a seat
        members = [
            {"security_id": f"B-{k:02d}", "sector_code": f"S{k:02d}"}
            for k in range(1, 22)
        ]
        select_sample(members)


def test_frame_normalization_sorts_and_hashes():
    frame = normalize_frame(
        [
            {"security_id": "B", "sector_code": "S2", "extra": "ignored"},
            {"security_id": "A", "sector_code": "S1"},
        ]
    )
    # only the two protocol fields survive, sorted by security_id
    assert [m["security_id"] for m in frame.members] == ["A", "B"]
    assert all(set(m) == {"security_id", "sector_code"} for m in frame.members)
    import hashlib

    expected = hashlib.sha256(
        '[{"sector_code":"S1","security_id":"A"},{"sector_code":"S2","security_id":"B"}]'.encode()
    ).hexdigest()
    assert frame.frame_sha256 == expected


def test_deterministic_and_manifest_shaped():
    a = select_sample(_k01_frame())
    b = select_sample(_k01_frame())
    assert a.selected == b.selected
    assert a.selected_list_sha256 == b.selected_list_sha256
    manifest = a.manifest()
    assert manifest["sampler_version"] == "sector-stratified-hash-v1"
    assert manifest["seed"] == "20260927"
    assert manifest["sample_size"] == 20
    assert manifest["frame_sha256"] == a.frame_sha256
    assert manifest["selected_list_sha256"] == K01_HASH
    assert manifest["quotas"] == a.quotas
    assert manifest["selected"] == a.selected
