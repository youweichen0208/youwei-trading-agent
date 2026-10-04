from __future__ import annotations
import uuid
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from quant.models import BASELINE_MODEL_VERSION, QUANT_MODEL_VERSION
from youwei_core.data.calendar import ET, next_trading_day, session_times, trading_day_offset
from youwei_core.data.securities import resolve_identifier
from youwei_core.db.meta import (
    campaigns,
    events,
    exploratory_research,
    exploratory_research_reports,
    jobs,
)
from youwei_core.jobs.service import (
    IdempotencyConflict,
    JobSubmission,
    RunSubmission,
    SubmitResult,
    submit_run,
)
from youwei_core.ledger.service import sha256_hex
from .models import (
    EVIDENCE_LOOKBACK_CALENDAR_DAYS,
    EXPLORATORY_CODE_VERSION,
    EXPLORATORY_JOB_KIND,
    EXPLORATORY_MAX_ATTEMPTS,
    ExploratoryNotFound,
    ExploratoryResearchError,
    ExploratorySubmitResult,
    IdempotencyConflictError,
    VALID_HORIZONS,
)

def build_config_manifest(*, target_spec_id: str, target_spec_sha256: str, horizon_td: int) -> dict:
    """The frozen research configuration recorded with every task.

    Comparisons that use this to SELECT a model or prompt must be
    pre-registered as a Trial (implementation plan S12); recording the
    manifest here makes such comparisons honest after the fact.
    """
    return {
        "code_version": EXPLORATORY_CODE_VERSION,
        "contracts_version": "research-v1",
        "quant": {
            "baseline_model_version": BASELINE_MODEL_VERSION,
            "quant_model_version": QUANT_MODEL_VERSION,
        },
        "target_spec": {"id": target_spec_id, "sha256": target_spec_sha256},
        "horizon_td": horizon_td,
        "evidence": {
            "kind": "daily_bars",
            "mode": "forward",
            "lookback_calendar_days": EVIDENCE_LOOKBACK_CALENDAR_DAYS,
        },
        "timezone": "America/New_York",
    }


async def submit_exploratory_research(
    engine,
    tenant_id: uuid.UUID,
    *,
    ticker: str,
    horizon_td: int,
    benchmark_ticker: str | None = None,
    idempotency_key: str,
) -> ExploratorySubmitResult:
    if horizon_td not in VALID_HORIZONS:
        raise ExploratoryResearchError(
            f"horizon_td must be one of {VALID_HORIZONS} (registered protocol set)"
        )
    if not idempotency_key:
        raise ExploratoryResearchError("idempotency_key is required")

    # 1. persistent run/job first: the jobs service owns idempotency for
    #    execution. The job payload echoes the request (NOT a generated
    #    id) so a same-key replay carries the identical payload hash.
    submission = RunSubmission(
        kind=EXPLORATORY_JOB_KIND,
        jobs=[
            JobSubmission(
                kind=EXPLORATORY_JOB_KIND,
                max_attempts=EXPLORATORY_MAX_ATTEMPTS,
                payload={
                    "ticker": ticker,
                    "horizon_td": horizon_td,
                    "benchmark_ticker": benchmark_ticker,
                },
            )
        ],
    )
    try:
        run: SubmitResult = await submit_run(engine, tenant_id, submission, idempotency_key)
    except IdempotencyConflict as exc:
        raise IdempotencyConflictError(str(exc)) from None

    # 2. existing task? replay resolves nothing — identifiers and even
    #    the active campaign may have moved since the first submission.
    existing = await _task_by_key(engine, tenant_id, idempotency_key)
    if existing is not None:
        return ExploratorySubmitResult(
            existing["id"], existing["run_id"], existing["job_id"], created=False
        )

    # 3. resolve the research context (read-only lookups)
    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()
        campaign = (
            (
                await conn.execute(
                    select(campaigns)
                    .where(
                        campaigns.c.tenant_id == tenant_id,
                        campaigns.c.status == "active",
                    )
                    .order_by(campaigns.c.created_at.asc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        job_id = (
            await conn.execute(select(jobs.c.id).where(jobs.c.run_id == run.run_id))
        ).scalars().one()
    if campaign is None:
        raise ExploratoryResearchError(
            "no active campaign for this tenant: exploratory research borrows "
            "its registered target specs and benchmark, and refuses to invent "
            "a research context"
        )
    spec = next(
        (s for s in campaign.target_specs if s.get("horizon_td") == horizon_td), None
    )
    if spec is None:
        raise ExploratoryResearchError(
            f"no registered target spec for horizon D{horizon_td}"
        )

    local_now = db_now.astimezone(ET)
    security_id = await resolve_identifier(
        engine, "ticker", ticker, as_of=local_now.date()
    )
    if security_id is None:
        raise ExploratoryResearchError(
            f"ticker {ticker!r} does not resolve to a security at "
            f"{local_now.date().isoformat()}"
        )
    if benchmark_ticker is not None:
        benchmark_id = await resolve_identifier(
            engine, "ticker", benchmark_ticker, as_of=local_now.date()
        )
        if benchmark_id is None:
            raise ExploratoryResearchError(
                f"benchmark ticker {benchmark_ticker!r} does not resolve to a "
                f"security at {local_now.date().isoformat()}"
            )
    else:
        benchmark_id = campaign.benchmark_security_id

    entry_date = await next_trading_day(engine, local_now.date())
    entry_session = await session_times(engine, entry_date)
    exit_date = await trading_day_offset(engine, entry_date, horizon_td)
    exit_session = await session_times(engine, exit_date)

    # 4. task row + event, atomically. The cutoff is the submission
    #    instant; the deadline is informational for the research brief
    #    (the question targets the window from entry — there is no seal
    #    deadline because nothing is sealed).
    manifest = build_config_manifest(
        target_spec_id=spec["target_spec_id"],
        target_spec_sha256=spec["content_sha256"],
        horizon_td=horizon_td,
    )
    research_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                exploratory_research.insert().values(
                    id=research_id,
                    tenant_id=tenant_id,
                    run_id=run.run_id,
                    job_id=job_id,
                    security_id=security_id,
                    benchmark_security_id=benchmark_id,
                    horizon_td=horizon_td,
                    target_spec_id=spec["target_spec_id"],
                    target_spec_sha256=spec["content_sha256"],
                    decision_cutoff_utc=db_now,
                    prediction_deadline_utc=entry_session.open_utc,
                    entry_at_utc=entry_session.open_utc,
                    exit_at_utc=exit_session.close_utc,
                    status="pending",
                    config_manifest=manifest,
                    config_sha256=sha256_hex(manifest),
                    idempotency_key=idempotency_key,
                )
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=tenant_id,
                    run_id=run.run_id,
                    event_type="exploratory.submitted",
                    payload={
                        "research_id": str(research_id),
                        "ticker": ticker,
                        "horizon_td": horizon_td,
                    },
                )
            )
    except IntegrityError:
        # concurrent same-key submit: the unique constraint picked a winner
        existing = await _task_by_key(engine, tenant_id, idempotency_key)
        if existing is None:
            raise
        return ExploratorySubmitResult(
            existing["id"], existing["run_id"], existing["job_id"], created=False
        )
    return ExploratorySubmitResult(research_id, run.run_id, job_id, created=True)


async def _task_by_key(engine, tenant_id: uuid.UUID, idempotency_key: str):
    async with engine.begin() as conn:
        return (
            (
                await conn.execute(
                    select(
                        exploratory_research.c.id,
                        exploratory_research.c.run_id,
                        exploratory_research.c.job_id,
                    ).where(
                        exploratory_research.c.tenant_id == tenant_id,
                        exploratory_research.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )


async def _current_tickers(engine, security_ids) -> dict:
    """Current ticker (valid_to is null) per security, for display."""
    from youwei_core.db.meta import security_identities

    if not security_ids:
        return {}
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    select(
                        security_identities.c.security_id,
                        security_identities.c.identifier,
                    ).where(
                        security_identities.c.security_id.in_(security_ids),
                        security_identities.c.identifier_type == "ticker",
                        security_identities.c.valid_to.is_(None),
                    )
                )
            )
            .mappings()
            .all()
        )
    return {row.security_id: row.identifier for row in rows}


async def list_exploratory_research(
    engine, tenant_id: uuid.UUID, *, limit: int = 50
) -> dict:
    """The tenant's exploratory research tasks, newest first, with the
    current tickers and the latest report version per task."""
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    select(
                        exploratory_research, jobs.c.status.label("job_status")
                    )
                    .select_from(
                        exploratory_research.join(
                            jobs, jobs.c.id == exploratory_research.c.job_id
                        )
                    )
                    .where(exploratory_research.c.tenant_id == tenant_id)
                    .order_by(exploratory_research.c.created_at.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        report_stats = {
            row.research_id: row
            for row in (
                (
                    await conn.execute(
                        select(
                            exploratory_research_reports.c.research_id,
                            func.max(
                                exploratory_research_reports.c.report_version
                            ).label("latest_version"),
                            func.count().label("versions"),
                        ).group_by(exploratory_research_reports.c.research_id)
                    )
                )
                .mappings()
                .all()
            )
        }
    tickers = await _current_tickers(
        engine,
        {r.security_id for r in rows} | {r.benchmark_security_id for r in rows},
    )
    return {
        "research": [
            {
                "research_id": str(r.id),
                "ticker": tickers.get(r.security_id) or str(r.security_id)[:8],
                "benchmark_ticker": tickers.get(r.benchmark_security_id) or "—",
                "horizon_td": r.horizon_td,
                "status": r.status,
                "job_status": r.job_status,
                "created_at": r.created_at.isoformat(),
                "latest_report_version": (
                    report_stats[r.id].latest_version if r.id in report_stats else None
                ),
                "report_versions": (
                    report_stats[r.id].versions if r.id in report_stats else 0
                ),
            }
            for r in rows
        ]
    }


async def get_exploratory_research(
    engine, tenant_id: uuid.UUID, research_id: uuid.UUID
) -> dict | None:
    """Tenant-scoped task view (with the executing run/job status for
    diagnosis); None when the research does not exist under this tenant
    (cross-tenant reads are misses, not leaks)."""
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    select(exploratory_research, jobs.c.status.label("job_status"))
                    .select_from(
                        exploratory_research.join(
                            jobs, jobs.c.id == exploratory_research.c.job_id
                        )
                    )
                    .where(
                        exploratory_research.c.id == research_id,
                        exploratory_research.c.tenant_id == tenant_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    view = {
        "research_id": str(row.id),
        "run_id": str(row.run_id),
        "job_id": str(row.job_id),
        "security_id": str(row.security_id),
        "benchmark_security_id": str(row.benchmark_security_id),
        "horizon_td": row.horizon_td,
        "target_spec_id": row.target_spec_id,
        "target_spec_sha256": row.target_spec_sha256,
        "decision_cutoff_utc": row.decision_cutoff_utc.isoformat(),
        "prediction_deadline_utc": row.prediction_deadline_utc.isoformat(),
        "entry_at_utc": row.entry_at_utc.isoformat(),
        "exit_at_utc": row.exit_at_utc.isoformat(),
        "status": row.status,
        "config_manifest": row.config_manifest,
        "config_sha256": row.config_sha256,
        "created_at": row.created_at.isoformat(),
        "job_status": row.job_status,
    }
    tickers = await _current_tickers(
        engine, {row.security_id, row.benchmark_security_id}
    )
    view["ticker"] = tickers.get(row.security_id) or str(row.security_id)[:8]
    view["benchmark_ticker"] = (
        tickers.get(row.benchmark_security_id) or "—"
    )
    return view


async def cancel_exploratory_research(
    engine, tenant_id: uuid.UUID, research_id: uuid.UUID
) -> dict:
    """Cancel an exploratory research task.

    Cancels the underlying run (the jobs fencing lands any in-flight
    attempt as late/cancelled — its report write is refused by
    ``save_exploratory_report``) and marks the task cancelled. A task
    that already produced a report stays succeeded: cancellation never
    rewrites history.
    """
    from youwei_core.jobs.service import RunNotFound, cancel_run

    async with engine.begin() as conn:
        task = (
            (
                await conn.execute(
                    select(exploratory_research).where(
                        exploratory_research.c.id == research_id,
                        exploratory_research.c.tenant_id == tenant_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if task is None:
        raise ExploratoryNotFound(str(research_id))
    if task.status in ("succeeded", "cancelled"):
        return {
            "research_id": str(research_id),
            "status": task.status,
            "already_terminal": True,
        }

    try:
        await cancel_run(engine, tenant_id, task.run_id)
    except RunNotFound:
        pass  # the run may have been reaped; the task still needs marking

    async with engine.begin() as conn:
        result = await conn.execute(
            exploratory_research.update()
            .where(
                exploratory_research.c.id == research_id,
                exploratory_research.c.status.in_(("pending", "running")),
            )
            .values(status="cancelled", updated_at=func.now())
        )
        if result.rowcount:
            await conn.execute(
                events.insert().values(
                    tenant_id=tenant_id,
                    run_id=task.run_id,
                    event_type="exploratory.cancelled",
                    payload={"research_id": str(research_id)},
                )
            )
    return {
        "research_id": str(research_id),
        "status": "cancelled",
        "already_terminal": False,
    }
