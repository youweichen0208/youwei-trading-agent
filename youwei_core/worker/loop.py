"""Worker loop: claim -> execute handler -> complete/fail, with a
heartbeat task and the outbox publisher.

The loop is the composition root for youwei-worker; all semantics
(leases, fencing, budgets) live in jobs.worker / budget.service and
are covered by their acceptance tests."""

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.auth.capability import sign_capability
from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs.outbox import publish_pending_events
from youwei_core.jobs.worker import (
    ClaimedJob,
    abandon_attempt,
    claim_next_job,
    complete_attempt,
    fail_attempt,
)

log = logging.getLogger("youwei.worker")

Handler = Callable[[ClaimedJob], Awaitable[dict]]


async def noop_handler(claimed: ClaimedJob) -> dict:
    return {"kind": claimed.kind, "noop": True}


class WorkerLoop:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        handlers: dict[str, Handler],
        settings: Settings | None = None,
    ):
        self.engine = engine
        self.handlers = handlers
        self.settings = settings or Settings()
        self.worker_id = f"worker-{id(self):x}"

    async def run_once(self, *, worker_id: str | None = None) -> bool:
        """Claim and execute one job with cancellation propagation:
        a watchdog heartbeats AND polls the job status; when the job
        stops running (cancelled mid-flight), the handler task is
        cancelled and the attempt lands 'cancelled'."""
        wid = worker_id or self.worker_id
        claimed = await claim_next_job(
            self.engine, wid, lease_ttl=self.settings.lease_ttl_seconds
        )
        if claimed is None:
            return False

        claimed.capability_token = sign_capability(
            self.settings.capability_secret,
            job_id=claimed.job_id,
            attempt_no=claimed.attempt_no,
            tenant_id=claimed.tenant_id,
            scopes=(f"job:{claimed.kind}", "snapshot_read", "llm_call"),
            exp=claimed.lease_expires_at,
        )

        handler = self.handlers.get(claimed.kind)
        if handler is None:
            await fail_attempt(
                self.engine, claimed.job_id, claimed.attempt_no,
                error=f"no handler registered for kind {claimed.kind!r}",
            )
            return True

        handler_task = asyncio.create_task(handler(claimed))
        watchdog = asyncio.create_task(
            self._watchdog_loop(claimed, wid, handler_task)
        )
        try:
            result = await handler_task
        except asyncio.CancelledError:
            log.info("handler cancelled mid-flight for job %s", claimed.job_id)
            await abandon_attempt(self.engine, claimed.job_id, claimed.attempt_no)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("handler failed for job %s: %s", claimed.job_id, exc)
            await fail_attempt(
                self.engine, claimed.job_id, claimed.attempt_no, error=str(exc)[:500]
            )
            return True
        finally:
            watchdog.cancel()

        outcome = await complete_attempt(
            self.engine, claimed.job_id, claimed.attempt_no, result
        )
        if outcome == "fenced":
            log.info(
                "late completion fenced for job %s attempt %s",
                claimed.job_id, claimed.attempt_no,
            )
        return True

    async def _watchdog_loop(
        self, claimed: ClaimedJob, worker_id: str, handler_task: asyncio.Task
    ) -> None:
        """Heartbeat + cancellation poll: extend the lease while the
        handler works, and cancel the handler as soon as its job stops
        running (run cancelled mid-flight)."""
        from sqlalchemy import select

        from youwei_core.db.meta import jobs as jobs_t

        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            await hb(
                self.engine,
                claimed.attempt_id,
                worker_id,
                lease_ttl=self.settings.lease_ttl_seconds,
            )
            async with self.engine.begin() as conn:
                status = (
                    await conn.execute(
                        select(jobs_t.c.status).where(jobs_t.c.id == claimed.job_id)
                    )
                ).scalar_one_or_none()
            if status != "running":
                log.info(
                    "job %s no longer running (status=%s); cancelling handler",
                    claimed.job_id, status,
                )
                handler_task.cancel()
                return

    async def _heartbeat_loop(self, claimed: ClaimedJob, worker_id: str) -> None:
        from youwei_core.jobs.worker import heartbeat as hb

        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            await hb(
                self.engine,
                claimed.attempt_id,
                worker_id,
                lease_ttl=self.settings.lease_ttl_seconds,
            )

    async def run_forever(self) -> None:
        """Claim loop + outbox publisher. Runs until cancelled."""
        publisher = asyncio.create_task(self._publisher_loop())
        try:
            while True:
                ran = await self.run_once()
                if not ran:
                    await asyncio.sleep(self.settings.worker_poll_interval_seconds)
        finally:
            publisher.cancel()

    async def _publisher_loop(self) -> None:
        while True:
            try:
                await publish_pending_events(self.engine)
            except Exception:  # noqa: BLE001 — publisher must never kill the worker
                log.exception("outbox publish failed; retrying")
            await asyncio.sleep(self.settings.event_publish_interval_seconds)


def main() -> None:
    """youwei-worker entrypoint."""
    from youwei_core.logfmt import configure_logging

    configure_logging()
    settings = Settings()
    engine = make_engine(
        settings.database_url,
        pool_size=settings.worker_pool_size,
        max_overflow=settings.worker_max_overflow,
    )
    loop = WorkerLoop(engine, handlers={"noop": noop_handler}, settings=settings)
    try:
        asyncio.run(loop.run_forever())
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
