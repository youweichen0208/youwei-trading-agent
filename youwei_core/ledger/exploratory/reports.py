from __future__ import annotations
import uuid
from sqlalchemy import func, select
from youwei_core.data.snapshots import read_snapshot
from youwei_core.db.meta import (
    attempts,
    events,
    exploratory_research,
    exploratory_research_reports,
    jobs,
)
from youwei_core.ledger.service import sha256_hex
from .models import EXPLORATORY_CODE_VERSION, REPORT_FORMAT, ReportSave

def _iso(value) -> str:
    return value.isoformat()


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
    research_attribution: dict | None = None,
    research_model_configured: str | None = None,
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
            # D2 版本留痕 (owner 2026-10-03): the configured routing AND what
            # actually ran — provider-returned model id, container image
            # digest, usage observation and exec-config version surfaced by
            # the runner research fetcher. None when the runner link was not
            # used (tests/local subprocess) or the checkout lacks the signal;
            # never fabricated.
            "research_model_configured": research_model_configured,
            "research_attribution": research_attribution,
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

