from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.market_cache import MarketCache
from yta_alpha.models import DataMode


def test_ttl_failure_retains_only_stale_metadata_and_recovers(snapshot, calendar):
    ticks = [0.0]
    calls = []
    broken = [False]

    def load():
        calls.append(1)
        if broken[0]:
            raise RuntimeError("private-provider-secret")
        return snapshot

    cache = MarketCache(
        load,
        DataMode.REPLAY,
        snapshot.provider,
        calendar,
        lambda: snapshot.as_of,
        ttl=10,
        monotonic=lambda: ticks[0],
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(r.status == "FRESH" for r in pool.map(lambda _: cache.read(), range(16)))
    assert len(calls) == 1
    broken[0] = True
    ticks[0] = 10
    stale = cache.read()
    assert stale.status == "STALE"
    assert stale.source_id == snapshot.source_id
    assert "secret" not in stale.model_dump_json()
    with pytest.raises(DomainError) as error:
        cache.read(require_fresh=True)
    assert error.value.code == "STALE_DATA"
    assert len(calls) == 2
    broken[0] = False
    ticks[0] = 20
    assert cache.read(require_fresh=True) == snapshot
    assert len(calls) == 3


def test_live_cache_never_accepts_replay_or_falls_back(snapshot, calendar):
    cache = MarketCache(
        lambda: snapshot,
        DataMode.LIVE_RESEARCH,
        snapshot.provider,
        calendar,
        lambda: snapshot.as_of,
    )
    result = cache.read()
    assert result.status == "UNAVAILABLE"
    assert result.data_snapshot_hash is None and result.source_id is None
    with pytest.raises(DomainError) as error:
        cache.read(require_fresh=True)
    assert error.value.code == "DATA_UNAVAILABLE"


def test_session_boundary_invalidates_before_ttl(snapshot, calendar):
    live = snapshot.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    clock = [snapshot.as_of]
    cache = MarketCache(
        lambda: live,
        DataMode.LIVE_RESEARCH,
        live.provider,
        calendar,
        lambda: clock[0],
        ttl=1000,
        monotonic=lambda: 0,
    )
    assert cache.read().status == "FRESH"
    # Weekend does not invent a session; Monday's close makes Friday's bar stale.
    clock[0] += timedelta(days=1)
    assert cache.read().status == "FRESH"
    clock[0] = calendar.close_at(calendar.after(snapshot.bars[-1].session, 1))
    assert cache.read().status == "STALE"
    assert cache.read().reason == "SESSION_OUTDATED"
    with pytest.raises(DomainError):
        cache.read(require_fresh=True)


@pytest.mark.parametrize("change", ["provider", "future", "invalid"])
def test_invalid_refresh_does_not_replace_valid_snapshot(snapshot, calendar, change):
    ticks = [0.0]
    value = [snapshot]
    cache = MarketCache(
        lambda: value[0],
        DataMode.REPLAY,
        snapshot.provider,
        calendar,
        lambda: snapshot.as_of,
        ttl=1,
        monotonic=lambda: ticks[0],
    )
    original = cache.read()
    if change == "provider":
        value[0] = snapshot.model_copy(update={"provider": "unexpected"})
    elif change == "future":
        value[0] = snapshot.model_copy(update={"as_of": snapshot.as_of - timedelta(days=1)})
    else:
        value[0] = {"private": "do-not-echo"}
    ticks[0] = 1
    result = cache.read()
    assert result.status == "STALE"
    assert result.data_snapshot_hash == original.data_snapshot_hash


@pytest.mark.parametrize("ttl", [0, -1, float("inf"), float("nan")])
def test_invalid_ttl_rejected(snapshot, calendar, ttl):
    with pytest.raises(ValueError):
        MarketCache(
            lambda: snapshot,
            DataMode.REPLAY,
            snapshot.provider,
            calendar,
            lambda: snapshot.as_of,
            ttl=ttl,
        )


def test_live_refresh_cannot_move_information_cutoff_backwards(snapshot, calendar):
    from yta_alpha.forecast import validate_snapshot

    live = snapshot.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    older = live.model_copy(update={"as_of": live.as_of - timedelta(minutes=1)})
    # Both versions are individually valid and belong to the same closed session.
    validate_snapshot(older, calendar)
    ticks = [0]
    value = [live]
    cache = MarketCache(
        lambda: value[0],
        DataMode.LIVE_RESEARCH,
        live.provider,
        calendar,
        lambda: live.as_of + timedelta(minutes=1),
        ttl=1,
        monotonic=lambda: ticks[0],
    )
    initial = cache.read()
    assert initial.status == "FRESH"
    value[0], ticks[0] = older, 1
    stale = cache.read()
    assert stale.status == "STALE" and stale.reason == "REFRESH_FAILED"
    assert stale.as_of == live.as_of
    assert stale.data_snapshot_hash == initial.data_snapshot_hash
    with pytest.raises(DomainError) as error:
        cache.read(require_fresh=True)
    assert error.value.code == "STALE_DATA"
    # An exact revalidation clears the failure; a later cutoff can then advance.
    value[0], ticks[0] = live, 2
    assert cache.read(require_fresh=True) == live
    newer = live.model_copy(update={"as_of": live.as_of + timedelta(minutes=1)})
    value[0], ticks[0] = newer, 3
    assert cache.read(require_fresh=True) == newer


def test_replay_can_move_its_isolated_logical_cutoff_backwards(snapshot, calendar):
    ticks = [0]
    value = [snapshot]
    cache = MarketCache(
        lambda: value[0],
        DataMode.REPLAY,
        snapshot.provider,
        calendar,
        lambda: snapshot.as_of,
        ttl=1,
        monotonic=lambda: ticks[0],
    )
    assert cache.read(require_fresh=True) == snapshot
    older = snapshot.model_copy(update={"as_of": snapshot.as_of - timedelta(minutes=1)})
    value[0], ticks[0] = older, 1
    assert cache.read(require_fresh=True) == older
