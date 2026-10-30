"""Due-settlement polling with append-only attempts and PostgreSQL execution leases.

Providers only read evidence. Financial arithmetic and outcome transactions stay in Alpha.
A crashed read may be retried; committed outcomes are never recomputed by this initial-settlement
queue. A separate revision workflow may explicitly submit newer evidence through ResearchStore.
"""

import argparse
import json
import random
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import REPLAY_BASE_SESSION, replay_observation
from yta_alpha.models import DataMode, Forecast, MarketSnapshot
from yta_alpha.qualified_market import QualifiedMarket
from yta_alpha.resolution import UnscorableEvidence, record_unscorable
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore
from yta_alpha.worker_health import WorkerHealth


class SettlementProvider(Protocol):
    def observation(
        self, forecast: Forecast, now: datetime
    ) -> MarketSnapshot | UnscorableEvidence | None:
        """Return verified evidence, None for delayed data, or raise on provider failure."""
        ...


class UnavailableProvider:
    def observation(
        self, forecast: Forecast, now: datetime
    ) -> MarketSnapshot | UnscorableEvidence | None:
        raise DomainError(
            "DATA_UNAVAILABLE", "No verified real provider configured", retryable=True
        )


class ReplayProvider:
    def __init__(self, calendar: TradingCalendar):
        self.calendar = calendar

    def observation(
        self, forecast: Forecast, now: datetime
    ) -> MarketSnapshot | UnscorableEvidence | None:
        if forecast.data_mode != DataMode.REPLAY or forecast.base_session != REPLAY_BASE_SESSION:
            raise DomainError("COHORT_MISMATCH", "Fixture supports only its explicit replay cohort")
        value = replay_observation(self.calendar)
        return value if value.as_of <= now else None


def retry_delay(attempt: int, jitter: float) -> timedelta:
    # Positive bounded jitter spreads provider recovery load; no financial values use floats.
    if attempt < 1 or not 0 <= jitter <= 1:
        raise ValueError("Invalid retry parameters")
    seconds = min(21600, 60 * 2 ** min(attempt - 1, 9))
    return timedelta(seconds=seconds * (0.75 + 0.25 * jitter))


class SettlementWorker:
    def __init__(
        self,
        store: ResearchStore,
        provider: SettlementProvider,
        mode: DataMode,
        *,
        dispatch_allowed: Callable[[], bool] | None = None,
    ):
        self.store, self.provider, self.mode = store, provider, mode
        self.dispatch_allowed = dispatch_allowed or (lambda: True)

    def queue_status(self, now: datetime, *, owner: UUID | None = None) -> dict:
        """Read current pending facts in one snapshot; never contacts a provider or writes."""
        if now.tzinfo is None:
            raise DomainError("INVALID_TIME", "Queue clock requires a timezone")
        with self.store._connect() as connection:
            row = connection.execute(
                "WITH due AS (SELECT p.id, "
                "(p.forecast->>'target_close_at')::timestamptz AS target_close_at, "
                "a.status, a.next_attempt_at FROM yta.predictions p LEFT JOIN LATERAL ("
                " SELECT status,next_attempt_at FROM yta.settlement_attempts "
                " WHERE prediction_id=p.id ORDER BY attempt_number DESC LIMIT 1"
                ") a ON true WHERE p.data_mode=%s AND p.created_at<=%s "
                "AND (p.forecast->>'target_close_at')::timestamptz<=%s "
                "AND (%s::uuid IS NULL OR p.owner_id=%s) "
                "AND NOT EXISTS (SELECT 1 FROM yta.prediction_outcomes WHERE prediction_id=p.id)) "
                "SELECT count(*) AS due_count, "
                "count(*) FILTER (WHERE next_attempt_at IS NULL OR next_attempt_at<=%s) "
                "AS eligible_count, "
                "count(*) FILTER (WHERE next_attempt_at>%s) AS backoff_count, "
                "count(*) FILTER (WHERE status='WAITING_DATA') AS waiting_data_count, "
                "count(*) FILTER (WHERE status='SETTLEMENT_FAILED') AS failed_count, "
                "count(*) FILTER (WHERE status IS NULL) AS unattempted_count, "
                "min(target_close_at) AS oldest_due_at FROM due",
                (self.mode.value, now, now, owner, owner, now, now),
            ).fetchone()
        oldest = row.pop("oldest_due_at")
        return {
            "schema_version": 1,
            "data_mode": self.mode.value,
            "scope": "owner" if owner is not None else "all_owners",
            "queue_time": now.isoformat(),
            **row,
            "oldest_due_at": oldest.isoformat() if oldest else None,
            "oldest_overdue_seconds": int((now - oldest).total_seconds()) if oldest else None,
        }

    def tick(self, now: datetime, *, limit: int = 100, owner: UUID | None = None) -> list[dict]:
        if now.tzinfo is None:
            raise DomainError("INVALID_TIME", "Worker clock requires a timezone")
        if not 1 <= limit <= 1000:
            raise ValueError("Worker batch limit must be between 1 and 1000")
        if not self.dispatch_allowed():
            return []
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.owner_id FROM yta.predictions p LEFT JOIN LATERAL ("
                " SELECT next_attempt_at FROM yta.settlement_attempts WHERE prediction_id=p.id"
                " ORDER BY attempt_number DESC LIMIT 1) a ON true "
                "WHERE p.data_mode=%s AND p.created_at<=%s "
                "AND (p.forecast->>'target_close_at')::timestamptz<=%s "
                "AND (%s::uuid IS NULL OR p.owner_id=%s) "
                "AND NOT EXISTS (SELECT 1 FROM yta.prediction_outcomes WHERE prediction_id=p.id) "
                "AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=%s) "
                "ORDER BY COALESCE(a.next_attempt_at, "
                "(p.forecast->>'target_close_at')::timestamptz), p.id LIMIT %s",
                (self.mode.value, now, now, owner, owner, now, limit),
            ).fetchall()
        results = []
        for row in rows:
            if not self.dispatch_allowed():
                break
            result = self.process(row["owner_id"], row["id"], now)
            if result:
                results.append(result)
        return results

    def process(self, owner: UUID, prediction_id: UUID, now: datetime) -> dict | None:
        if now.tzinfo is None:
            raise DomainError("INVALID_TIME", "Worker clock requires a timezone")
        if not self.dispatch_allowed():
            return None
        # Connection-scoped lock is released even on process death. No fact-table UPDATE needed.
        with self.store._connect() as lease:
            lease.autocommit = True
            acquired = lease.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired",
                (f"settlement-worker:{prediction_id}",),
            ).fetchone()["acquired"]
            if not acquired:
                return None
            try:
                saved = self.store.get_prediction(owner, prediction_id)
                forecast = Forecast.model_validate(saved["forecast"])
                if forecast.data_mode != self.mode:
                    raise DomainError("COHORT_MISMATCH", "Worker cannot cross data modes")
                if saved["outcomes"] or forecast.target_close_at > now:
                    return None
                previous = lease.execute(
                    "SELECT attempt_number,next_attempt_at FROM yta.settlement_attempts "
                    "WHERE prediction_id=%s ORDER BY attempt_number DESC LIMIT 1",
                    (prediction_id,),
                ).fetchone()
                if previous and previous["next_attempt_at"] and previous["next_attempt_at"] > now:
                    return None
                # Once provider execution starts, let reconciliation/commit finish. A stop
                # request prevents the next dispatch; it cannot undo an in-flight outcome.
                if not self.dispatch_allowed():
                    return None
                number = previous["attempt_number"] + 1 if previous else 1
                code = None
                try:
                    observation = self.provider.observation(forecast, now)
                    if observation is None:
                        status = "WAITING_DATA"
                    elif isinstance(observation, UnscorableEvidence):
                        settled = record_unscorable(
                            self.store, owner, prediction_id, observation, now=now
                        )
                        status = settled["outcomes"][-1]["status"]
                    else:
                        settled = self.store.settle_prediction(
                            owner, prediction_id, observation, now=now
                        )
                        status = "SETTLED" if settled["outcomes"] else "WAITING_DATA"
                except DomainError as error:
                    status = "WAITING_DATA" if error.code == "WAITING_DATA" else "SETTLEMENT_FAILED"
                    # Persist only known code categories, never provider exception text or secrets.
                    code = (
                        error.code
                        if error.code
                        in {
                            "DATA_UNAVAILABLE",
                            "WAITING_DATA",
                            "COHORT_MISMATCH",
                            "FUTURE_INFORMATION",
                            "INVALID_DATA",
                            "INVALID_TIME",
                            "STALE_DATA",
                            "NOT_DUE",
                        }
                        else "INVALID_OBSERVATION"
                    )
                except Exception:
                    status, code = "SETTLEMENT_FAILED", "PROVIDER_ERROR"
                attempt_id = uuid4()
                with self.store._connect() as connection:
                    self.store._lock(connection, f"settle:{prediction_id}")
                    current = self.store._prediction(connection, owner, prediction_id)
                    # A simultaneous manual settlement wins over a delayed failed provider read.
                    if current["outcomes"]:
                        status, code = current["outcomes"][-1]["status"], None
                    retry_at = (
                        None
                        if status in ("SETTLED", "UNSCORABLE")
                        else now + retry_delay(number, random.random())
                    )
                    if status not in ("SETTLED", "UNSCORABLE"):
                        self.store._event(
                            connection,
                            prediction_id,
                            status,
                            f"attempt:{attempt_id}",
                            {"error_code": code, "attempt_number": number},
                            now,
                        )
                    connection.execute(
                        "INSERT INTO yta.settlement_attempts "
                        "(id,prediction_id,attempt_number,status,error_code,started_at,finished_at,"
                        "next_attempt_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (attempt_id, prediction_id, number, status, code, now, now, retry_at),
                    )
                    connection.execute(
                        "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                        "VALUES (%s,'settlement.attempt',%s,%s)",
                        (
                            owner,
                            str(prediction_id),
                            Jsonb(
                                {
                                    "attempt_id": str(attempt_id),
                                    "status": status,
                                    "error_code": code,
                                }
                            ),
                        ),
                    )
                return {
                    "prediction_id": str(prediction_id),
                    "attempt_number": number,
                    "status": status,
                    "error_code": code,
                    "next_attempt_at": retry_at.isoformat() if retry_at else None,
                }
            finally:
                lease.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                    (f"settlement-worker:{prediction_id}",),
                )


def run_worker(
    worker, *, watch, interval, limit, owner, replay_at, stop, health=None, dispatch_enabled=True
):
    """Refresh health only after a completed batch; hung batches eventually become stale."""
    failed = True
    try:
        if health:
            health.write("starting")
        while not stop.is_set():
            results = (
                worker.tick(replay_at or datetime.now(UTC), limit=limit, owner=owner)
                if dispatch_enabled
                else []
            )
            queue = worker.queue_status(replay_at or datetime.now(UTC), owner=owner)
            dispatch_status = (
                "STOPPING" if stop.is_set() else "OPEN" if dispatch_enabled else "PAUSED"
            )
            if health:
                health.write("ready" if dispatch_status == "OPEN" else dispatch_status.lower())
            print(
                json.dumps(
                    {
                        "data_mode": worker.mode.value,
                        "dispatch_status": dispatch_status,
                        "results": results,
                        "queue": queue,
                    }
                ),
                flush=True,
            )
            if not watch or stop.wait(interval):
                break
        failed = False
    finally:
        if health:
            health.write("failed" if failed else "stopped")


def main():
    parser = argparse.ArgumentParser(description="Run the initial due-settlement queue")
    parser.add_argument(
        "--watch", action="store_true", help="Poll until stopped; default: one batch"
    )
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--heartbeat-file", type=Path)
    parser.add_argument(
        "--queue-only", action="store_true", help="Read queue metrics without processing"
    )
    parser.add_argument("--owner", type=UUID)
    parser.add_argument("--replay-at", type=datetime.fromisoformat)
    args = parser.parse_args()
    if args.queue_only and (args.watch or args.heartbeat_file):
        parser.error("Queue-only cannot watch or write a heartbeat")
    settings = Settings()
    if settings.database_url is None:
        parser.error("Database configuration required")
    if args.interval < 1:
        parser.error("Interval must be positive")
    if not 1 <= args.limit <= 1000:
        parser.error("Batch limit must be between 1 and 1000")
    if args.replay_at is not None and args.replay_at.tzinfo is None:
        parser.error("Replay clock requires a timezone")
    calendar = TradingCalendar()
    if settings.data_mode == DataMode.REPLAY:
        if settings.env not in ("development", "test") or args.replay_at is None:
            parser.error("Replay worker requires development/test and explicit --replay-at")
        provider = ReplayProvider(calendar)
    else:
        if args.replay_at:
            parser.error("Real mode cannot override the clock")
        provider = (
            QualifiedMarket(settings.database_url.get_secret_value(), calendar, settings.data_mode)
            if settings.market_provider == "MASSIVE"
            else UnavailableProvider()
        )
    store = ResearchStore(settings.database_url.get_secret_value(), calendar, settings.git_sha)
    stop = Event()
    worker = SettlementWorker(
        store,
        provider,
        settings.data_mode,
        dispatch_allowed=lambda: settings.jobs_dispatch == "OPEN" and not stop.is_set(),
    )
    health = (
        WorkerHealth(
            args.heartbeat_file, settings.data_mode.value, isinstance(provider, ReplayProvider)
        )
        if args.heartbeat_file
        else None
    )
    previous = {
        sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        if args.queue_only:
            print(
                json.dumps(
                    worker.queue_status(args.replay_at or datetime.now(UTC), owner=args.owner)
                )
            )
            return
        run_worker(
            worker,
            watch=args.watch,
            interval=args.interval,
            limit=args.limit,
            owner=args.owner,
            replay_at=args.replay_at,
            stop=stop,
            health=health,
            dispatch_enabled=settings.jobs_dispatch == "OPEN",
        )
    except Exception:
        # Database/provider/configuration exceptions may contain private connection details.
        print(json.dumps({"error_code": "WORKER_FAILED"}), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
