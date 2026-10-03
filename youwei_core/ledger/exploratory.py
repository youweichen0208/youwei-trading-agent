"""S12a: user-initiated exploratory research — the shared Core capability
behind the S12 product loop (Open WebUI entry, Dashboard viewing).

Exploratory research answers one prediction-shaped question against
frozen evidence WITHOUT the formal prediction ledger: no Campaign,
ForecastCase or prediction rows are ever created. The task row itself is
the research context (security, benchmark, registered target spec,
calendar-resolved windows) plus a config manifest frozen at submit time.
The research turn runs through the same controlled runtime as Phase 1B
(FrozenEvidence incl. the quant prediction, reference validation,
Runner/subprocess boundary).

Submission is tenant-scoped and idempotent per (tenant, Idempotency-Key):
the persistent run/job is created through the battle-tested jobs
submission (its payload echoes the request, so a replay under the same
key sees the same payload hash), then the task row + its event are
written atomically. A crash between the two heals on replay.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from quant.models import BASELINE_MODEL_VERSION, QUANT_MODEL_VERSION
from youwei_core.data.calendar import (
    ET,
    next_trading_day,
    session_times,
    trading_day_offset,
    tzdb_version,
)
from youwei_core.data.securities import resolve_identifier
from youwei_core.data.snapshots import freeze_daily_bars, read_snapshot
from youwei_core.db.meta import (
    attempts,
    calendar_builds,
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

EXPLORATORY_CODE_VERSION = "exploratory-research-v1"
EXPLORATORY_JOB_KIND = "research.exploratory"
EXPLORATORY_MAX_ATTEMPTS = 3
REPORT_FORMAT = "exploratory-report-v1"
VALID_HORIZONS = (1, 20, 60)
EVIDENCE_LOOKBACK_CALENDAR_DAYS = 90


class ExploratoryResearchError(Exception):
    """The research submission cannot proceed (bad request or missing
    registered context); never a research execution failure."""


class ExploratoryNotFound(Exception):
    """The research id does not exist under this tenant."""


class IdempotencyConflictError(Exception):
    """Same idempotency key with a different request."""


@dataclass
class ExploratorySubmitResult:
    research_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    created: bool


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


# --- execution (S12a backend loop) ------------------------------------------
#
# submit -> persistent job -> freeze input -> controlled research ->
# validate references -> save report -> query. The research turn itself
# goes through the same controlled boundary as Phase 1B (Runner research
# client or the local agent-runtime subprocess); tests inject a mock
# fetcher at the same seam. Exploratory results NEVER touch the formal
# prediction ledger (no Campaign / ForecastCase / prediction writes).


class ExploratoryExecutionError(Exception):
    """Infrastructure-level failure of the execution loop itself."""


@dataclass
class ReportSave:
    report_version: int
    created: bool
    fenced: bool


def _iso(value) -> str:
    return value.isoformat()


async def _lease_expiry(claimed) -> "datetime":
    """The Runner research grant must not outlive the attempt's lease."""
    from datetime import UTC

    expires = claimed.lease_expires_at
    return expires.astimezone(UTC) if expires.tzinfo else expires


async def _attempt_is_current(conn, claimed) -> bool:
    """The seal-pattern fence: the attempt must still be this job's
    current running attempt with a live lease (stale workers can never
    write business state)."""
    from sqlalchemy import func as sa_func

    row = (
        (
            await conn.execute(
                select(
                    attempts.c.status,
                    attempts.c.lease_expires_at,
                    jobs.c.attempt_count,
                    jobs.c.status.label("job_status"),
                    jobs.c.tenant_id,
                )
                .select_from(attempts)
                .join(jobs, jobs.c.id == attempts.c.job_id)
                .where(
                    attempts.c.id == claimed.attempt_id,
                    attempts.c.attempt_no == claimed.attempt_no,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    db_now = (await conn.execute(select(sa_func.clock_timestamp()))).scalar_one()
    return not (
        row is None
        or row.attempt_count != claimed.attempt_no
        or row.job_status != "running"
        or row.status != "running"
        or row.lease_expires_at <= db_now
        or row.tenant_id != claimed.tenant_id
    )


async def _set_task_status(
    engine, claimed, research_id: uuid.UUID, status: str, event_type: str, payload: dict
) -> bool:
    """Fenced task status transition + event in one transaction. A stale
    attempt (cancelled, lease-lost, superseded) never overwrites the
    task's state."""
    async with engine.begin() as conn:
        if not await _attempt_is_current(conn, claimed):
            return False
        await conn.execute(
            exploratory_research.update()
            .where(
                exploratory_research.c.id == research_id,
                exploratory_research.c.status != "succeeded",
            )
            .values(status=status, updated_at=func.now())
        )
        await conn.execute(
            events.insert().values(
                tenant_id=claimed.tenant_id,
                run_id=claimed.run_id,
                event_type=event_type,
                payload=payload,
            )
        )
    return True


def build_report_content(
    *,
    task,
    snapshot: dict,
    bars_by_security: dict,
    baseline,
    quant,
    proposal,
    calendar_manifest: dict,
) -> dict:
    """Assemble the report content (pure; the summary / evidence / quant /
    counter-evidence / limitations / version blocks). References stay as
    locators — the read API resolves them against the frozen snapshot so
    the report never embeds mutable copies."""
    manifest = snapshot["manifest"]
    query = manifest.get("query", {})
    content = {
        "report_format": REPORT_FORMAT,
        "research_id": str(task.id),
        "question": {
            "security_id": str(task.security_id),
            "benchmark_security_id": str(task.benchmark_security_id),
            "horizon_td": task.horizon_td,
            "target_spec": {"id": task.target_spec_id, "sha256": task.target_spec_sha256},
            "decision_cutoff_utc": _iso(task.decision_cutoff_utc),
            "entry_at_utc": _iso(task.entry_at_utc),
            "exit_at_utc": _iso(task.exit_at_utc),
        },
        "summary": {
            "source_status": proposal.source_status,
            "p_outperform": proposal.p_outperform,
            "expected_excess_return": proposal.expected_excess_return,
            "quant_relation": proposal.quant_relation,
            "basis": proposal.quantitative_basis,
        },
        "evidence": {
            "snapshot_id": str(snapshot["id"]),
            "kind": manifest.get("kind", "daily_bars"),
            "as_of": query.get("as_of"),
            "mode": query.get("mode"),
            "content_sha256": manifest.get("content_sha256"),
            "row_counts": {
                sec: len(rows) for sec, rows in bars_by_security.items()
            },
            "manifest": manifest,
        },
        "quant": {
            "baseline": baseline.model_dump(mode="json"),
            "quant_model": quant.model_dump(mode="json"),
        },
        "counter_evidence": {
            "warnings": [w.model_dump(mode="json") for w in proposal.warnings],
            "missing": list(proposal.missing),
            "quantitative_basis": proposal.quantitative_basis,
        },
        "research": {"proposal": proposal.model_dump(mode="json")},
        "config": {
            "manifest": task.config_manifest,
            "sha256": task.config_sha256,
            "calendar": calendar_manifest,
        },
        "versions": {
            "report_format": REPORT_FORMAT,
            "code_version": EXPLORATORY_CODE_VERSION,
            "contracts_version": "research-v1",
            "quant_model_version": quant.model_version,
            "research_model": (
                proposal.model.model_dump(mode="json") if proposal.model else None
            ),
        },
    }
    limitations = [
        "exploratory research: not a sealed prediction; excluded from the "
        "formal ledger and from forward evaluation",
        "single research turn over frozen daily bars; no news, filings or "
        "fundamentals are in evidence",
    ]
    for sec, rows in sorted(bars_by_security.items()):
        if not rows:
            limitations.append(
                f"no usable bars for {sec} in the frozen evidence window"
            )
    for warning in proposal.warnings:
        limitations.append(f"research warning: {warning.kind}: {warning.detail}")
    if proposal.missing:
        limitations.append(
            "researcher could not ground: " + ", ".join(proposal.missing)
        )
    if quant.source_status != "produced":
        limitations.append(
            f"quant model unavailable ({quant.reason}); the research answered "
            "independently of a quant position"
        )
    content["limitations"] = limitations
    return content


async def save_exploratory_report(
    engine,
    claimed,
    research_id: uuid.UUID,
    content: dict,
    evidence_snapshot_id: uuid.UUID,
) -> ReportSave:
    """Append the report (fenced, content-idempotent).

    Same content sha as the latest version -> no new version (a job
    retry that reproduces the research does not pollute history).
    Different content -> a new appended version; old versions stay
    readable. A fenced attempt saves nothing."""
    content_sha = sha256_hex(content)
    async with engine.begin() as conn:
        if not await _attempt_is_current(conn, claimed):
            return ReportSave(report_version=0, created=False, fenced=True)
        latest = (
            (
                await conn.execute(
                    select(
                        exploratory_research_reports.c.report_version,
                        exploratory_research_reports.c.content_sha256,
                    )
                    .where(exploratory_research_reports.c.research_id == research_id)
                    .order_by(exploratory_research_reports.c.report_version.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if latest is not None and latest.content_sha256 == content_sha:
            version, created = latest.report_version, False
        else:
            version = (latest.report_version + 1) if latest else 1
            await conn.execute(
                exploratory_research_reports.insert().values(
                    id=uuid.uuid4(),
                    research_id=research_id,
                    tenant_id=claimed.tenant_id,
                    report_version=version,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    evidence_snapshot_id=evidence_snapshot_id,
                    content=content,
                    content_sha256=content_sha,
                )
            )
            created = True
        await conn.execute(
            exploratory_research.update()
            .where(exploratory_research.c.id == research_id)
            .values(status="succeeded", updated_at=func.now())
        )
        await conn.execute(
            events.insert().values(
                tenant_id=claimed.tenant_id,
                run_id=claimed.run_id,
                event_type="exploratory.report_saved",
                payload={
                    "research_id": str(research_id),
                    "report_version": version,
                    "created": created,
                    "content_sha256": content_sha,
                },
            )
        )
    return ReportSave(report_version=version, created=created, fenced=False)


async def _calendar_manifest(engine, entry_at_utc) -> dict:
    """The batch-manifest-shaped calendar block the research brief
    expects (version + hash + tzdb), resolved from the entry session."""
    entry_session = await session_times(
        engine, entry_at_utc.astimezone(ET).date()
    )
    async with engine.begin() as conn:
        build = (
            (
                await conn.execute(
                    select(
                        calendar_builds.c.version,
                        calendar_builds.c.content_sha256,
                    ).where(calendar_builds.c.version == entry_session.build_version)
                )
            )
            .mappings()
            .one()
        )
    return {
        "calendar_version": build.version,
        "calendar_sha256": build.content_sha256,
        "tzdb": tzdb_version(),
        "context": "exploratory",
    }


def make_exploratory_research_handler(
    engine,
    *,
    fetcher_factory=None,
    runner_research=None,
    agent_runtime=None,
):
    """Build the ``research.exploratory`` job handler.

    ``fetcher_factory`` (tests) is ``(claimed, task, snapshot, quant) ->
    fetch_proposal(case, bars, quant)``. Production wiring mirrors the
    batch pipeline: ``runner_research`` (Runner-controlled container) or
    ``agent_runtime`` (local subprocess). Without any wiring the task
    fails honestly — research is never fabricated.
    """

    async def handle_exploratory(claimed) -> dict:
        from youwei_core.jobs.worker import ClaimedJob  # noqa: F401 — typing aid

        from youwei_core.ledger.evidence import build_frozen_evidence
        from youwei_core.ledger.pipeline import (
            predict_baseline,
            predict_quant,
        )
        from youwei_contracts.research import validate_proposal_references

        async with engine.begin() as conn:
            task = (
                (
                    await conn.execute(
                        select(exploratory_research).where(
                            exploratory_research.c.run_id == claimed.run_id,
                            exploratory_research.c.tenant_id == claimed.tenant_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if task is None:
            raise ExploratoryExecutionError(
                f"no exploratory research task for run {claimed.run_id}"
            )

        await _set_task_status(
            engine, claimed, task.id, "running", "exploratory.running",
            {"research_id": str(task.id), "attempt_no": claimed.attempt_no},
        )

        cutoff = task.decision_cutoff_utc
        security_ids = [task.security_id, task.benchmark_security_id]
        start_date = (cutoff - timedelta(days=EVIDENCE_LOOKBACK_CALENDAR_DAYS)).date()
        snap = await freeze_daily_bars(
            engine,
            security_ids,
            start_date,
            cutoff.date(),
            as_of=cutoff,
            mode="forward",
            created_by_attempt=claimed.attempt_id,
        )
        snapshot = await read_snapshot(engine, snap.snapshot_id)

        import json as _json

        raw_content = snapshot["content"]
        bars_by_security: dict = {}
        for bar in (
            _json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        ):
            bars_by_security.setdefault(bar["security_id"], []).append(bar)
        for rows in bars_by_security.values():
            rows.sort(key=lambda b: b.get("trade_date", ""))
        own_bars = bars_by_security.get(str(task.security_id), [])

        baseline = predict_baseline(own_bars)
        quant = predict_quant(own_bars)
        if quant.evidence_snapshot_id is None:
            quant = quant.model_copy(
                update={"evidence_snapshot_id": snap.snapshot_id}
            )

        case = {
            "id": task.id,
            "security_id": task.security_id,
            "benchmark_security_id": task.benchmark_security_id,
            "horizon_td": task.horizon_td,
            "target_spec_id": task.target_spec_id,
            "target_spec_sha256": task.target_spec_sha256,
            "decision_cutoff_utc": task.decision_cutoff_utc,
            "prediction_deadline_utc": task.prediction_deadline_utc,
            "entry_at_utc": task.entry_at_utc,
            "exit_at_utc": task.exit_at_utc,
        }
        calendar_manifest = await _calendar_manifest(engine, task.entry_at_utc)

        fetch = None
        if fetcher_factory is not None:
            fetch = fetcher_factory(claimed, task, snapshot, quant)
        elif runner_research is not None:
            from youwei_core.ledger.research_client import make_runner_research_fetcher

            fetch = make_runner_research_fetcher(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                job_id=claimed.job_id,
                attempt_no=claimed.attempt_no,
                snapshot=snapshot,
                batch_manifest=calendar_manifest,
                client=runner_research.client,
                key=runner_research.key,
                expiry_provider=lambda: _lease_expiry(claimed),
                research_config=runner_research.research_config,
            )
        elif agent_runtime is not None and claimed.capability_token is not None:
            from youwei_core.ledger.pipeline import make_phase1b_llm_fetcher

            fetch = make_phase1b_llm_fetcher(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                snapshot=snapshot,
                batch_manifest=calendar_manifest,
                capability_token=claimed.capability_token,
                agent_runtime=agent_runtime,
            )

        if fetch is None:
            reason = "no research wiring configured for exploratory research"
            await _set_task_status(
                engine, claimed, task.id, "failed", "exploratory.failed",
                {"research_id": str(task.id), "reason": reason},
            )
            return {"research_id": str(task.id), "status": "failed", "reason": reason}

        try:
            proposal = await fetch(case, own_bars, quant)
        except Exception as exc:  # noqa: BLE001 — research failure -> failed task
            reason = f"research_runtime_error: {type(exc).__name__}: {str(exc)[:200]}"
            await _set_task_status(
                engine, claimed, task.id, "failed", "exploratory.failed",
                {"research_id": str(task.id), "reason": reason},
            )
            return {"research_id": str(task.id), "status": "failed", "reason": reason}
        if proposal is None:
            await _set_task_status(
                engine, claimed, task.id, "failed", "exploratory.failed",
                {"research_id": str(task.id), "reason": "research_unavailable"},
            )
            return {
                "research_id": str(task.id), "status": "failed",
                "reason": "research_unavailable",
            }

        # Controller-side validation: binding, quant_relation, reference
        # resolution — the same discipline the formal path enforces.
        try:
            evidence = build_frozen_evidence(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                case=case,
                snapshot=snapshot,
                batch_manifest=calendar_manifest,
                quant=quant,
            )
            validate_proposal_references(evidence, proposal)
        except ValueError as exc:
            reason = f"proposal_rejected: {str(exc)[:300]}"
            await _set_task_status(
                engine, claimed, task.id, "failed", "exploratory.failed",
                {"research_id": str(task.id), "reason": reason},
            )
            return {"research_id": str(task.id), "status": "failed", "reason": reason}

        content = build_report_content(
            task=task,
            snapshot=snapshot,
            bars_by_security=bars_by_security,
            baseline=baseline,
            quant=quant,
            proposal=proposal,
            calendar_manifest=calendar_manifest,
        )
        save = await save_exploratory_report(
            engine, claimed, task.id, content, snap.snapshot_id
        )
        if save.fenced:
            return {
                "research_id": str(task.id), "status": "fenced",
                "reason": "attempt superseded or cancelled; report not saved",
            }
        return {
            "research_id": str(task.id),
            "status": "succeeded",
            "report_version": save.report_version,
            "report_created": save.created,
        }

    return handle_exploratory


async def get_exploratory_report(
    engine,
    tenant_id: uuid.UUID,
    research_id: uuid.UUID,
    *,
    version: int | None = None,
) -> dict | None:
    """Latest (or specific) report version, tenant-scoped, with the
    proposal's references resolved against the frozen snapshot — the
    report stores locators, resolution happens against immutable
    evidence so citations stay checkable."""
    async with engine.begin() as conn:
        stmt = select(exploratory_research_reports).where(
            exploratory_research_reports.c.research_id == research_id,
            exploratory_research_reports.c.tenant_id == tenant_id,
        )
        if version is not None:
            stmt = stmt.where(exploratory_research_reports.c.report_version == version)
        stmt = stmt.order_by(
            exploratory_research_reports.c.report_version.desc()
        ).limit(1)
        report = (await conn.execute(stmt)).mappings().first()
        if report is None:
            return None
        latest_version = (
            await conn.execute(
                select(func.max(exploratory_research_reports.c.report_version)).where(
                    exploratory_research_reports.c.research_id == research_id
                )
            )
        ).scalar_one()
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
            .one()
        )

    from youwei_contracts.research import (
        ResearchReference,
        resolve_reference,
    )

    from youwei_core.ledger.evidence import build_frozen_evidence

    content = report.content
    snapshot = await read_snapshot(engine, report.evidence_snapshot_id)
    evidence = build_frozen_evidence(
        run_id=task.run_id,
        tenant_id=tenant_id,
        case={
            "id": task.id,
            "security_id": task.security_id,
            "benchmark_security_id": task.benchmark_security_id,
            "horizon_td": task.horizon_td,
            "target_spec_id": task.target_spec_id,
            "target_spec_sha256": task.target_spec_sha256,
            "decision_cutoff_utc": task.decision_cutoff_utc,
            "prediction_deadline_utc": task.prediction_deadline_utc,
            "entry_at_utc": task.entry_at_utc,
            "exit_at_utc": task.exit_at_utc,
        },
        snapshot=snapshot,
        batch_manifest=content["config"]["calendar"],
        quant=content["quant"]["quant_model"],
    )
    resolved = []
    for ref in content["research"]["proposal"].get("references", []):
        reference = ResearchReference.model_validate(ref)
        resolved.append(
            {
                "locator": reference.locator,
                "note": reference.note,
                "row": resolve_reference(evidence, reference),
            }
        )
    return {
        "research_id": str(research_id),
        "report_version": report.report_version,
        "content_sha256": report.content_sha256,
        "created_at": report.created_at.isoformat(),
        "is_latest": report.report_version == latest_version,
        "content": content,
        "references_resolved": resolved,
    }


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
