"""Worker loop: claim -> execute handler -> complete/fail, with a
heartbeat task and the outbox publisher.

The loop is the composition root for youwei-worker; all semantics
(leases, fencing, budgets) live in jobs.worker / budget.service and
are covered by their acceptance tests."""

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs.outbox import publish_pending_events
from youwei_core.jobs.worker import ClaimedJob, claim_next_job, complete_attempt, fail_attempt

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
        """Claim and execute one job. Returns True when a job ran."""
        wid = worker_id or self.worker_id
        claimed = await claim_next_job(
            self.engine, wid, lease_ttl=self.settings.lease_ttl_seconds
        )
        if claimed is None:
            return False

        handler = self.handlers.get(claimed.kind)
        if handler is None:
            await fail_attempt(
                self.engine, claimed.job_id, claimed.attempt_no,
                error=f"no handler registered for kind {claimed.kind!r}",
            )
            return True

        heartbeat = asyncio.create_task(
            self._heartbeat_loop(claimed, wid)
        )
        try:
            result = await handler(claimed)
        except Exception as exc:  # noqa: BLE001 — handler failures become attempt failures
            log.warning("handler failed for job %s: %s", claimed.job_id, exc)
            heartbeat.cancel()
            await fail_attempt(
                self.engine, claimed.job_id, claimed.attempt_no, error=str(exc)[:500]
            )
            return True
        heartbeat.cancel()

        outcome = await complete_attempt(
            self.engine, claimed.job_id, claimed.attempt_no, result
        )
        if outcome == "fenced":
            log.info(
                "late completion fenced for job %s attempt %s",
                claimed.job_id, claimed.attempt_no,
            )
        return True

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
    logging.basicConfig(level=logging.INFO)
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
