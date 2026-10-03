"""S12a: exploratory research submission and querying.

Exploratory research is the user-initiated research loop OUTSIDE the
formal prediction ledger: no Campaign or ForecastCase rows are created —
the task row itself is the research context (security, benchmark,
horizon, registered target spec, calendar-resolved windows) with a
config manifest frozen at submit time. Submission is tenant-scoped,
idempotent, and creates the persistent run/job the worker executes.
"""

import uuid

import pytest

from youwei_core.ledger.exploratory import (
    ExploratoryResearchError,
    IdempotencyConflictError,
    get_exploratory_research,
    submit_exploratory_research,
)
from test_ledger_campaign import _setup


async def test_submit_resolves_ticker_and_freezes_context(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=2)
    result = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
    )
    assert result.created
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task is not None
    assert task["security_id"] == str(ctx["panel"][0])
    # benchmark defaults to the active campaign's benchmark
    assert task["benchmark_security_id"] == str(ctx["benchmark"])
    assert task["horizon_td"] == 20
    assert task["target_spec_id"] == "excess-tr-d20-v1"
    assert task["target_spec_sha256"]
    assert task["status"] == "pending"
    # calendar-resolved windows: entry strictly after the cutoff, exit after entry
    from datetime import datetime

    cutoff = datetime.fromisoformat(task["decision_cutoff_utc"])
    entry = datetime.fromisoformat(task["entry_at_utc"])
    exit_ = datetime.fromisoformat(task["exit_at_utc"])
    deadline = datetime.fromisoformat(task["prediction_deadline_utc"])
    assert entry > cutoff
    assert exit_ > entry
    # the deadline is informational for the brief (answered for the window)
    assert deadline == entry
    # config manifest frozen at submit with a content hash
    assert task["config_manifest"]["code_version"] == "exploratory-research-v1"
    assert task["config_manifest"]["target_spec"]["id"] == "excess-tr-d20-v1"
    assert task["config_manifest"]["quant"]["quant_model_version"]
    assert len(task["config_sha256"]) == 64
    # persistent run + job for the worker
    assert task["run_id"] == str(result.run_id)
    assert task["job_id"] == str(result.job_id)


async def test_submit_is_idempotent_per_tenant_key(db_engine, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    a = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
    )
    b = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
    )
    assert a.created and not b.created
    assert a.research_id == b.research_id
    assert a.run_id == b.run_id


async def test_submit_conflicts_when_payload_differs_under_same_key(db_engine, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=2)
    await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
    )
    with pytest.raises(IdempotencyConflictError):
        await submit_exploratory_research(
            db_engine, tenant_id, ticker="S1", horizon_td=20, idempotency_key="k1",
        )


async def test_submit_rejects_unknown_ticker(db_engine, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    with pytest.raises(ExploratoryResearchError, match="ticker"):
        await submit_exploratory_research(
            db_engine, tenant_id, ticker="NOPE", horizon_td=20, idempotency_key="k1",
        )


async def test_submit_rejects_unregistered_horizon(db_engine, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    # horizons are constrained to the registered protocol set (1/20/60);
    # anything else is refused before touching the calendar or campaign
    with pytest.raises(ExploratoryResearchError, match="horizon"):
        await submit_exploratory_research(
            db_engine, tenant_id, ticker="S0", horizon_td=7, idempotency_key="k1",
        )


async def test_submit_requires_active_campaign_context(db_engine, tenant_id):
    """No active campaign -> no registered target spec / benchmark source:
    the service refuses rather than inventing a research context."""
    from test_ledger_campaign import _build_calendar, _security

    await _build_calendar(db_engine)
    await _security(db_engine, ticker="S0")
    with pytest.raises(ExploratoryResearchError, match="campaign"):
        await submit_exploratory_research(
            db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
        )


async def test_benchmark_ticker_override(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=2)
    result = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=1,
        benchmark_ticker="S1", idempotency_key="k1",
    )
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task["benchmark_security_id"] == str(ctx["panel"][1])


async def test_get_is_tenant_scoped(db_engine, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    result = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1",
    )
    other = uuid.uuid4()
    assert await get_exploratory_research(db_engine, other, result.research_id) is None


# --- API surface ------------------------------------------------------------


async def test_api_submit_requires_auth(client, db_engine, tenant_id):
    r = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={"Idempotency-Key": "k1"},
    )
    assert r.status_code == 401


async def test_api_submit_creates_and_replays(client, db_engine, tenant_headers, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    r = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["target_spec_id"] == "excess-tr-d20-v1"

    replay = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    assert replay.status_code == 200
    assert replay.json()["research_id"] == body["research_id"]


async def test_api_submit_conflict_is_409(client, db_engine, tenant_headers, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=2)
    headers = {**tenant_headers, "Idempotency-Key": "k1"}
    await client.post("/v1/research", json={"ticker": "S0", "horizon_td": 20}, headers=headers)
    r = await client.post("/v1/research", json={"ticker": "S1", "horizon_td": 20}, headers=headers)
    assert r.status_code == 409


async def test_api_submit_unknown_ticker_is_422(client, db_engine, tenant_headers, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    r = await client.post(
        "/v1/research",
        json={"ticker": "NOPE", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    assert r.status_code == 422


async def test_api_submit_requires_idempotency_key(client, db_engine, tenant_headers, tenant_id):
    await _setup(db_engine, tenant_id, n_panel=1)
    r = await client.post(
        "/v1/research", json={"ticker": "S0", "horizon_td": 20}, headers=tenant_headers,
    )
    assert r.status_code == 422


# --- execution loop: submit -> job -> freeze -> research -> report ----------

import json as _json

from sqlalchemy import text

from youwei_contracts.research import ResearchProposal
from youwei_core.jobs.worker import claim_next_job, complete_attempt, fail_attempt
from youwei_core.ledger.exploratory import (
    cancel_exploratory_research,
    get_exploratory_report,
    make_exploratory_research_handler,
)
from test_ledger_outcomes import _ingest_window


def _mock_factory(proposal_builder):
    """Test seam: fetcher_factory(claimed, task, snapshot, quant)."""

    def factory(claimed, task, snapshot, quant):
        async def fetch(case, bars, quant_arg):
            return proposal_builder(claimed, case, snapshot, quant_arg)

        return fetch

    return factory


def _kept_proposal(claimed, case, snapshot, quant):
    content = (
        _json.loads(snapshot["content"])
        if isinstance(snapshot["content"], str)
        else snapshot["content"]
    )
    kwargs = {}
    if quant.source_status == "produced":
        kwargs["quant_relation"] = "kept"
    return ResearchProposal(
        run_id=claimed.run_id,
        case_id=case["id"],
        source_status="produced",
        p_outperform=0.6,
        expected_excess_return=0.02,
        references=(
            [{"kind": "evidence", "locator": f"snapshot:{snapshot['id']}/rows/0"}]
            if content
            else []
        ),
        quantitative_basis="momentum agrees with the frozen window",
        model={"model_version": "mock-llm", "provider": "mock"},
        **kwargs,
    )


async def _seed_bars(engine, ctx, *, days=30, close=101.0):
    async with engine.begin() as conn:
        dates = [
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT date FROM calendar_days WHERE venue='XNYS' AND is_trading "
                        "AND date <= current_date ORDER BY date DESC LIMIT :n"
                    ),
                    {"n": days},
                )
            ).all()
        ]
    dates.reverse()
    await _ingest_window(
        engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": close}
    )
    await _ingest_window(
        engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0}
    )
    return dates


async def _run_once(engine, tenant_id, *, factory, key="k1", ticker="S0", horizon=20):
    result = await submit_exploratory_research(
        engine, tenant_id, ticker=ticker, horizon_td=horizon, idempotency_key=key
    )
    claimed = await claim_next_job(engine, "test-worker")
    assert claimed is not None and claimed.kind == "research.exploratory"
    handler = make_exploratory_research_handler(engine, fetcher_factory=factory)
    summary = await handler(claimed)
    return result, claimed, summary, handler


async def test_execution_loop_saves_report_with_resolvable_references(
    db_engine, tenant_id
):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    # ledger tables are empty before any exploratory run (the campaign
    # fixture only registers chain-head lock state, no predictions)
    ledger_tables = (
        "predictions",
        "forecast_commits",
        "forecast_cases",
        "forecast_batches",
    )
    async with db_engine.begin() as conn:
        before = {
            t: (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()
            for t in ledger_tables
        }
        chain_head = (
            await conn.execute(text("SELECT head_seq FROM ledger_chains"))
        ).scalar_one()
    result, claimed, summary, _ = await _run_once(
        db_engine, tenant_id, factory=_mock_factory(_kept_proposal)
    )
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)

    assert summary["status"] == "succeeded"
    assert summary["report_version"] == 1
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task["status"] == "succeeded"

    report = await get_exploratory_report(db_engine, tenant_id, result.research_id)
    assert report is not None and report["is_latest"] is True
    content = report["content"]
    # the required blocks: summary / evidence / quant / counter-evidence /
    # limitations / versions
    assert content["summary"]["p_outperform"] == 0.6
    assert content["summary"]["quant_relation"] == "kept"
    assert content["evidence"]["content_sha256"]
    assert content["evidence"]["row_counts"] == {
        str(ctx["panel"][0]): 30,
        str(ctx["benchmark"]): 30,
    }
    assert content["quant"]["quant_model"]["source_status"] == "produced"
    assert content["quant"]["quant_model"]["p_outperform"] is not None
    assert content["quant"]["baseline"]["p_outperform"] == 0.5
    assert content["counter_evidence"]["quantitative_basis"] == (
        "momentum agrees with the frozen window"
    )
    assert any("exploratory research" in lim for lim in content["limitations"])
    assert content["versions"]["code_version"] == "exploratory-research-v1"
    assert content["config"]["sha256"] == task["config_sha256"]
    # references resolve against the frozen snapshot (checkable citations)
    assert report["references_resolved"]
    resolved_ids = {r["row"]["security_id"] for r in report["references_resolved"]}
    assert resolved_ids <= {str(ctx["panel"][0]), str(ctx["benchmark"])}

    # exploratory results never touch the formal prediction ledger
    async with db_engine.begin() as conn:
        for table in ledger_tables:
            n = (
                await conn.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
            assert n == before[table], f"{table} gained rows from exploratory research"
        assert (
            await conn.execute(text("SELECT head_seq FROM ledger_chains"))
        ).scalar_one() == chain_head


async def test_no_research_wiring_fails_honestly(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    result, claimed, summary, _ = await _run_once(db_engine, tenant_id, factory=None)
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)
    assert summary["status"] == "failed"
    assert "no research wiring" in summary["reason"]
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task["status"] == "failed"
    assert await get_exploratory_report(db_engine, tenant_id, result.research_id) is None


async def test_research_exception_fails_task_without_report(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)

    def boom_factory(claimed, task, snapshot, quant):
        async def fetch(case, bars, quant_arg):
            raise RuntimeError("gateway down")

        return fetch

    result, claimed, summary, _ = await _run_once(
        db_engine, tenant_id, factory=boom_factory
    )
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)
    assert summary["status"] == "failed"
    assert "research_runtime_error" in summary["reason"]
    assert await get_exploratory_report(db_engine, tenant_id, result.research_id) is None


async def test_invalid_proposal_is_rejected_without_report(db_engine, tenant_id):
    """A proposal that ignores the frozen evidence (unresolvable reference
    or a missing quant_relation) is rejected at the write boundary."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)

    def bad_proposal(claimed, case, snapshot, quant):
        return ResearchProposal(  # no quant_relation over a produced quant
            run_id=claimed.run_id,
            case_id=case["id"],
            source_status="produced",
            p_outperform=0.6,
            expected_excess_return=0.02,
            references=[
                {"kind": "evidence", "locator": f"snapshot:{snapshot['id']}/rows/999"}
            ],
            quantitative_basis="invented",
            model={"model_version": "mock-llm", "provider": "mock"},
        )

    result, claimed, summary, _ = await _run_once(
        db_engine, tenant_id, factory=_mock_factory(bad_proposal)
    )
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)
    assert summary["status"] == "failed"
    assert "proposal_rejected" in summary["reason"]
    assert await get_exploratory_report(db_engine, tenant_id, result.research_id) is None


async def test_insufficient_bars_quant_unavailable_reported_honestly(db_engine, tenant_id):
    """With no usable bars the quant position is unavailable; the research
    may still answer independently (no quant_relation) and the report
    records the quant absence instead of hiding it."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx, days=5)  # < momentum's 21-bar minimum

    def independent_proposal(claimed, case, snapshot, quant):
        assert quant.source_status == "unavailable"
        return ResearchProposal(
            run_id=claimed.run_id,
            case_id=case["id"],
            source_status="unavailable",
            reason="no bars in evidence",
        )

    result, claimed, summary, _ = await _run_once(
        db_engine, tenant_id, factory=_mock_factory(independent_proposal)
    )
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)
    assert summary["status"] == "succeeded"
    report = await get_exploratory_report(db_engine, tenant_id, result.research_id)
    assert report["content"]["quant"]["quant_model"]["source_status"] == "unavailable"
    assert any("quant model unavailable" in lim for lim in report["content"]["limitations"])


# --- retry / recovery / fencing ---------------------------------------------


async def test_retry_with_identical_content_does_not_append_version(db_engine, tenant_id):
    """Crash after the report write, before job completion: the retry
    reproduces the same research, the content hash matches and no second
    version appears (job-level idempotency of the report)."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    factory = _mock_factory(_kept_proposal)
    result, claimed1, summary1, handler = await _run_once(
        db_engine, tenant_id, factory=factory
    )
    assert summary1["status"] == "succeeded"
    # simulate: worker died AFTER the report write, before completion
    await fail_attempt(
        db_engine, claimed1.job_id, claimed1.attempt_no,
        error="simulated crash after report write",
    )
    claimed2 = await claim_next_job(db_engine, "test-worker")
    assert claimed2 is not None and claimed2.attempt_no == 2
    summary2 = await handler(claimed2)
    await complete_attempt(db_engine, claimed2.job_id, claimed2.attempt_no, summary2)
    assert summary2["status"] == "succeeded"
    assert summary2["report_version"] == 1
    assert summary2["report_created"] is False


async def test_different_content_appends_version_and_old_stays_readable(
    db_engine, tenant_id
):
    """The SAME research task, retried with a different research outcome,
    appends a new version; the old version stays readable."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    factory = _mock_factory(_kept_proposal)
    result, claimed1, summary1, handler = await _run_once(
        db_engine, tenant_id, factory=factory, key="k1"
    )
    assert summary1["status"] == "succeeded"
    # crash after the report write, before job completion
    await fail_attempt(
        db_engine, claimed1.job_id, claimed1.attempt_no,
        error="simulated crash after report write",
    )

    # the retry's research turn returns a different, adjusted proposal
    def adjusted_proposal(claimed, case, snapshot, quant):
        proposal = _kept_proposal(claimed, case, snapshot, quant)
        return proposal.model_copy(
            update={"p_outperform": 0.55, "quant_relation": "adjusted"}
        )

    handler_adjusted = make_exploratory_research_handler(
        db_engine, fetcher_factory=_mock_factory(adjusted_proposal)
    )
    claimed2 = await claim_next_job(db_engine, "test-worker")
    assert claimed2 is not None and claimed2.attempt_no == 2
    summary2 = await handler_adjusted(claimed2)
    await complete_attempt(db_engine, claimed2.job_id, claimed2.attempt_no, summary2)
    assert summary2["status"] == "succeeded"
    assert summary2["report_version"] == 2
    assert summary2["report_created"] is True

    latest = await get_exploratory_report(db_engine, tenant_id, result.research_id)
    assert latest["report_version"] == 2
    assert latest["is_latest"] is True
    assert latest["content"]["summary"]["p_outperform"] == 0.55
    old = await get_exploratory_report(
        db_engine, tenant_id, result.research_id, version=1
    )
    assert old["report_version"] == 1
    assert old["is_latest"] is False
    assert old["content"]["summary"]["p_outperform"] == 0.6


async def test_cancelled_run_fences_the_report_write(db_engine, tenant_id):
    """Cancel mid-flight: the attempt may still finish computing, but its
    report write is fenced — no report, task cancelled, nothing in the
    formal ledger."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    result = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1"
    )
    claimed = await claim_next_job(db_engine, "test-worker")
    # cancel while the research turn is "in flight"
    outcome = await cancel_exploratory_research(db_engine, tenant_id, result.research_id)
    assert outcome["status"] == "cancelled"

    handler = make_exploratory_research_handler(
        db_engine, fetcher_factory=_mock_factory(_kept_proposal)
    )
    summary = await handler(claimed)  # the late attempt still runs to completion
    assert summary["status"] == "fenced"
    assert await get_exploratory_report(db_engine, tenant_id, result.research_id) is None
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task["status"] == "cancelled"


async def test_expired_lease_fences_the_stale_attempt(db_engine, tenant_id):
    """Timeout semantics: an attempt whose lease expired cannot write the
    report; the re-claimed attempt (restart recovery) completes it."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    result = await submit_exploratory_research(
        db_engine, tenant_id, ticker="S0", horizon_td=20, idempotency_key="k1"
    )
    claimed1 = await claim_next_job(db_engine, "test-worker")
    # simulate the worker dying: the attempt lease expires underneath it
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE attempts SET lease_expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": str(claimed1.attempt_id)},
        )
    handler = make_exploratory_research_handler(
        db_engine, fetcher_factory=_mock_factory(_kept_proposal)
    )
    stale = await handler(claimed1)  # completes after the lease expired
    assert stale["status"] == "fenced"

    # restart recovery: the reaper re-queues the job, a new attempt runs
    claimed2 = await claim_next_job(db_engine, "test-worker")
    assert claimed2 is not None and claimed2.attempt_no == 2
    summary2 = await handler(claimed2)
    await complete_attempt(db_engine, claimed2.job_id, claimed2.attempt_no, summary2)
    assert summary2["status"] == "succeeded"
    report = await get_exploratory_report(db_engine, tenant_id, result.research_id)
    assert report["report_version"] == 1
    task = await get_exploratory_research(db_engine, tenant_id, result.research_id)
    assert task["status"] == "succeeded"


# --- API: query / report / cancel -------------------------------------------


async def test_api_query_report_and_cancel_flow(client, db_engine, tenant_headers, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)

    r = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    research_id = r.json()["research_id"]

    # status view while pending
    status = await client.get(f"/v1/research/{research_id}", headers=tenant_headers)
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["job_status"] == "queued"

    # no report yet -> 404 (honest absence, never a fake empty report)
    report = await client.get(
        f"/v1/research/{research_id}/report", headers=tenant_headers
    )
    assert report.status_code == 404

    # execute the job
    claimed = await claim_next_job(db_engine, "test-worker")
    handler = make_exploratory_research_handler(
        db_engine, fetcher_factory=_mock_factory(_kept_proposal)
    )
    summary = await handler(claimed)
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)

    report = await client.get(
        f"/v1/research/{research_id}/report", headers=tenant_headers
    )
    assert report.status_code == 200
    body = report.json()
    assert body["report_version"] == 1
    assert body["is_latest"] is True
    assert body["content"]["summary"]["p_outperform"] == 0.6
    assert body["references_resolved"]

    # explicit old/new version reads
    v1 = await client.get(
        f"/v1/research/{research_id}/report?version=1", headers=tenant_headers
    )
    assert v1.status_code == 200 and v1.json()["report_version"] == 1
    missing = await client.get(
        f"/v1/research/{research_id}/report?version=7", headers=tenant_headers
    )
    assert missing.status_code == 404

    # cancel is a no-op on the succeeded task (history never rewritten)
    cancel = await client.post(
        f"/v1/research/{research_id}/cancel", headers=tenant_headers
    )
    assert cancel.status_code == 200
    assert cancel.json() == {
        "research_id": research_id,
        "status": "succeeded",
        "already_terminal": True,
    }


async def test_api_cancel_pending_research(client, db_engine, tenant_headers, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    r = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    research_id = r.json()["research_id"]
    cancel = await client.post(
        f"/v1/research/{research_id}/cancel", headers=tenant_headers
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    status = await client.get(f"/v1/research/{research_id}", headers=tenant_headers)
    assert status.json()["status"] == "cancelled"
    # the job is cancelled too — no worker will pick it up
    assert status.json()["job_status"] == "cancelled"


async def test_api_tenant_isolation(client, db_engine, tenant_headers, other_tenant_headers, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    await _seed_bars(db_engine, ctx)
    r = await client.post(
        "/v1/research",
        json={"ticker": "S0", "horizon_td": 20},
        headers={**tenant_headers, "Idempotency-Key": "k1"},
    )
    research_id = r.json()["research_id"]
    # another tenant sees nothing: status, report and cancel are all 404
    assert (await client.get(
        f"/v1/research/{research_id}", headers=other_tenant_headers
    )).status_code == 404
    assert (await client.get(
        f"/v1/research/{research_id}/report", headers=other_tenant_headers
    )).status_code == 404
    assert (await client.post(
        f"/v1/research/{research_id}/cancel", headers=other_tenant_headers
    )).status_code == 404


async def test_api_query_requires_auth(client, db_engine):
    r = await client.get("/v1/research/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 401
