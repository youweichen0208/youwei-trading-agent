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
    heartbeat as hb,
)
from youwei_core.ledger.pipeline import AgentRuntimeConfig

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
        scheduler_fn: Callable[[], Awaitable] | None = None,
    ):
        self.engine = engine
        self.handlers = handlers
        self.settings = settings or Settings()
        self.scheduler_fn = scheduler_fn
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
            await self._record_confirmed_cancellation(claimed)
            # A worker shutdown must leave run_forever, while cancellation of
            # only the handler by our watchdog lets the worker claim again.
            if asyncio.current_task().cancelling():
                raise
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("handler failed for job %s: %s", claimed.job_id, exc)
            await fail_attempt(
                self.engine, claimed.job_id, claimed.attempt_no, error=str(exc)[:500]
            )
            return True
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)

        outcome = await complete_attempt(
            self.engine, claimed.job_id, claimed.attempt_no, result
        )
        if outcome == "fenced":
            log.info(
                "late completion fenced for job %s attempt %s",
                claimed.job_id, claimed.attempt_no,
            )
        return True

    async def _record_confirmed_cancellation(self, claimed: ClaimedJob) -> None:
        """Only a cancelled job authorizes a terminal cancelled attempt.

        Lease loss, worker shutdown, or unavailable storage leave the attempt
        running so the normal lease reaper can recover the active job.
        """
        from sqlalchemy import select

        from youwei_core.db.meta import jobs

        try:
            async with self.engine.begin() as conn:
                status = (await conn.execute(
                    select(jobs.c.status).where(jobs.c.id == claimed.job_id)
                )).scalar_one_or_none()
            if status == "cancelled":
                await abandon_attempt(self.engine, claimed.job_id, claimed.attempt_no)
        except Exception:
            log.exception("cannot confirm job cancellation; leaving attempt for lease recovery")

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
            try:
                alive = await hb(self.engine, claimed.attempt_id, worker_id,
                                 lease_ttl=self.settings.lease_ttl_seconds)
            except Exception:
                log.exception("lease renewal failed; cancelling handler")
                handler_task.cancel()
                return
            if not alive:
                handler_task.cancel()
                return
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
        """Claim loop + outbox publisher + scheduler tick. Runs until
        cancelled."""
        publisher = asyncio.create_task(self._publisher_loop())
        scheduler = (
            asyncio.create_task(self._scheduler_loop())
            if self.scheduler_fn is not None
            else None
        )
        try:
            while True:
                ran = await self.run_once()
                if not ran:
                    await asyncio.sleep(self.settings.worker_poll_interval_seconds)
        finally:
            publisher.cancel()
            if scheduler is not None:
                scheduler.cancel()

    async def _scheduler_loop(self) -> None:
        """Periodic Controller-side tick (batch planning, prediction
        submission, outcome resolution, reports). Every step is
        idempotent, so a crashed or overlapping tick is harmless;
        errors never kill the worker."""
        while True:
            try:
                await self.scheduler_fn()
            except Exception:  # noqa: BLE001 — scheduler must never kill the worker
                log.exception("scheduler tick failed; retrying")
            await asyncio.sleep(self.settings.scheduler_interval_seconds)

    async def _publisher_loop(self) -> None:
        while True:
            try:
                await publish_pending_events(self.engine)
            except Exception:  # noqa: BLE001 — publisher must never kill the worker
                log.exception("outbox publish failed; retrying")
            await asyncio.sleep(self.settings.event_publish_interval_seconds)


def main() -> None:
    """youwei-worker entrypoint: noop + data collection + prediction
    + sandbox handlers, plus the scheduler tick."""
    from youwei_core.data.tiingo import TiingoClient, make_tiingo_daily_handler
    from youwei_core.ledger.pipeline import make_batch_predict_handler
    from youwei_core.ledger.scheduler import scheduler_tick
    from youwei_core.logfmt import configure_logging
    from youwei_core.sandbox.handler import make_sandbox_handler
    from youwei_core.sandbox.client import RunnerClient

    configure_logging()
    settings = Settings()
    engine = make_engine(
        settings.database_url,
        pool_size=settings.worker_pool_size,
        max_overflow=settings.worker_max_overflow,
    )
    tiingo = TiingoClient(
        settings.tiingo_token,
        base_url=settings.tiingo_base_url,
        min_interval=settings.tiingo_min_request_interval_seconds,
    )
    runner = RunnerClient(settings.runner_url) if settings.runner_url else None

    agent_runtime = _build_agent_runtime(settings)
    handlers = {
        "noop": noop_handler,
        "data.tiingo_daily": make_tiingo_daily_handler(engine, tiingo),
        "research.batch_predict": make_batch_predict_handler(
            engine, agent_runtime=agent_runtime
        ),
    }
    if runner is not None:
        handlers["sandbox.execute"] = make_sandbox_handler(engine, runner, secret=settings.runner_secret)
    loop = WorkerLoop(
        engine,
        handlers=handlers,
        settings=settings,
        scheduler_fn=lambda: scheduler_tick(engine),
    )
    async def serve():
        try:
            await loop.run_forever()
        finally:
            if runner is not None:
                await runner.aclose()
            await tiingo.aclose()
            await engine.dispose()

    asyncio.run(serve())


def _build_agent_runtime(settings: Settings) -> AgentRuntimeConfig | None:
    """Assemble the agent-runtime subprocess wiring from Settings, or
    None when the agent runtime is disabled (Phase 1A / no LLM budget)."""
    from youwei_core.ledger.agent_client import build_process_factory

    if not settings.agent_runtime_enabled:
        return None
    command = settings.agent_runtime_command.split()
    if not command:
        return None
    env = {}
    if settings.agent_runtime_pythonpath:
        env["PYTHONPATH"] = settings.agent_runtime_pythonpath
    return AgentRuntimeConfig(
        research_config={
            "base_url": settings.agent_llm_base_url,
            "api_key": settings.agent_llm_api_key,
            "model": settings.agent_llm_model,
        },
        process_factory=build_process_factory(command, env),
        timeout_seconds=settings.agent_runtime_timeout_seconds,
    )


if __name__ == "__main__":
    main()
