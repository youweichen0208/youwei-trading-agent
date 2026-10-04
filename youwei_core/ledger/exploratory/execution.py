from __future__ import annotations
from datetime import timedelta
from sqlalchemy import select
from youwei_core.data.calendar import ET, session_times, tzdb_version
from youwei_core.data.snapshots import freeze_daily_bars, read_snapshot
from youwei_core.db.meta import calendar_builds, exploratory_research
from .models import EVIDENCE_LOOKBACK_CALENDAR_DAYS, ExploratoryExecutionError
from .reports import _set_task_status, build_report_content, save_exploratory_report

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
        research_attribution: dict = {}
        if fetcher_factory is not None:
            fetch = fetcher_factory(claimed, task, snapshot, quant)
        elif runner_research is not None:
            from youwei_core.ledger.experiment_orchestrator import active_lease_expiry
            from youwei_core.ledger.research_client import make_runner_research_fetcher

            # D2 版本留痕: collect what actually ran (provider-returned model
            # id, image digest, usage, exec-config version) alongside the
            # configured routing for the report's versions block.
            research_attribution.clear()
            fetch = make_runner_research_fetcher(
                run_id=claimed.run_id,
                tenant_id=claimed.tenant_id,
                job_id=claimed.job_id,
                attempt_no=claimed.attempt_no,
                snapshot=snapshot,
                batch_manifest=calendar_manifest,
                client=runner_research.client,
                key=runner_research.key,
                expiry_provider=lambda: active_lease_expiry(
                    engine, claimed.attempt_id, claimed.attempt_no
                ),
                research_config=runner_research.research_config,
                attribution_sink=research_attribution.update,
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
            research_attribution=dict(research_attribution) or None,
            research_model_configured=(
                runner_research.research_config.get("model")
                if runner_research is not None
                else None
            ),
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
