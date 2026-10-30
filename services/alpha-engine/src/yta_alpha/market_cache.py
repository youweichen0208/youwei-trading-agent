"""Bounded process-local snapshot cache. Stale facts are metadata-only, never research inputs."""

import math
import time
from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import Literal

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import snapshot_hash, validate_snapshot
from yta_alpha.models import Contract, DataMode, MarketSnapshot


class MarketFreshness(Contract):
    status: Literal["FRESH", "STALE", "UNAVAILABLE"]
    data_mode: DataMode
    cache_key: str
    ttl_seconds: float
    source_id: str | None
    data_snapshot_hash: str | None
    as_of: datetime | None
    reason: Literal["REFRESH_FAILED", "SESSION_OUTDATED"] | None


class MarketCache:
    def __init__(
        self,
        loader: Callable[[], MarketSnapshot],
        mode: DataMode,
        provider: str,
        calendar: TradingCalendar,
        clock: Callable[[], datetime],
        *,
        ttl: float = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Cache TTL must be positive and finite")
        self.loader, self.mode, self.provider = loader, mode, provider
        self.calendar, self.clock, self.monotonic, self.ttl = calendar, clock, monotonic, ttl
        self.key = f"{mode.value}:NVDA:{provider}:{calendar.version}:raw-market-v1"
        self._lock = Lock()
        self._snapshot: MarketSnapshot | None = None
        self._checked: float | None = None
        self._failed = False

    def _current(self, snapshot: MarketSnapshot) -> bool:
        now = snapshot.as_of if self.mode != DataMode.LIVE_RESEARCH else self.clock()
        return snapshot.as_of <= now and snapshot.bars[-1].session == self.calendar.latest_closed(
            now
        )

    def read(self, *, require_fresh: bool = False) -> MarketFreshness | MarketSnapshot:
        with self._lock:
            now = self.monotonic()
            if self._checked is None or now < self._checked or now - self._checked >= self.ttl:
                try:
                    loaded = self.loader()
                    candidate = MarketSnapshot.model_validate(
                        loaded.model_dump(mode="json")
                        if isinstance(loaded, MarketSnapshot)
                        else loaded
                    )
                    if candidate.data_mode != self.mode or candidate.provider != self.provider:
                        raise ValueError("Provider or mode mismatch")
                    validate_snapshot(candidate, self.calendar)
                    # Keep the last valid version on invalid/late refreshes.
                    if not self._current(candidate):
                        raise ValueError("Snapshot is not current")
                    if (
                        self.mode == DataMode.LIVE_RESEARCH
                        and self._snapshot is not None
                        and candidate.as_of < self._snapshot.as_of
                    ):
                        raise ValueError("Live snapshot information cutoff regressed")
                    self._snapshot = candidate
                    self._failed = False
                except Exception:
                    self._failed = True
                self._checked = self.monotonic()
            snapshot = self._snapshot
            current = False
            if snapshot is not None:
                try:
                    current = self._current(snapshot)
                except DomainError:
                    current = False
            status = (
                "UNAVAILABLE"
                if snapshot is None
                else "FRESH"
                if current and not self._failed
                else "STALE"
            )
            reason = (
                "REFRESH_FAILED" if self._failed else "SESSION_OUTDATED" if not current else None
            )
            if require_fresh:
                if status != "FRESH":
                    raise DomainError(
                        "STALE_DATA" if snapshot else "DATA_UNAVAILABLE",
                        "Verified current market data is unavailable",
                        status=503,
                    )
                return snapshot
            return MarketFreshness(
                status=status,
                data_mode=self.mode,
                cache_key=self.key,
                ttl_seconds=self.ttl,
                source_id=snapshot.source_id if snapshot else None,
                data_snapshot_hash=snapshot_hash(snapshot) if snapshot else None,
                as_of=snapshot.as_of if snapshot else None,
                reason=reason,
            )
