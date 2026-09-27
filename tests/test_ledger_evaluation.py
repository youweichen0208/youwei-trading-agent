"""S05c: batch evaluation reports.

Acceptance mapped from the plan (S05) and campaign-policy §4:
- the primary metric is the paired Brier delta over the pairable
  subset (on_time commit, produced baseline AND quant, resolved
  outcome); the subset mean is never a claim about missing cases
- the report discloses the full planned denominator: no-commit,
  late/uncertain commits, unavailable sources, unresolved/unscorable
  outcomes
- y = 1(excess > 0), exactly 0 counts as false
- no pairable case -> NA (None), never a fabricated 0
- the report fixes case/commit/outcome-revision references, release
  and scoring code version; corrections append new versions and old
  reports keep their references; identical heads regenerate
  identically
- generation waits for maturity and definite case states (never
  forever: the resolver's grace policy decides those)
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from youwei_core.ledger.evaluation import (
    BatchNotFound,
    NotReady,
    generate_batch_report,
    latest_report,
)
from youwei_core.ledger.outcomes import record_unscorable, resolve_outcome
from youwei_core.ledger.service import plan_batch
from youwei_core.data.calendar import next_weekly_cutoff
from test_ledger_campaign import _setup
from test_ledger_outcomes import _ingest_window, _retarget_case, _window_dates
from test_ledger_seal import _seal, _shift_case_window, _sources


async def _scenario(engine, tenant_id, *, n_panel=6, exit_sessions_back=10):
    """A batch whose D20 cases exercise every coverage path.

    Per panel security (D20 case):
      S0 pairable (on_time, produced pair, resolved +)
      S1 quant unavailable (baseline-only scorable)
      S2 no commit at all (outcome still resolved)
      S3 late-confirmed commit (excluded from on_time)
      S4 no bars -> unresolved outcome
      S5 unscorable (evidence)
    """
    ctx = await _setup(engine, tenant_id, n_panel=n_panel)
    plan = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # case order is panel-major, horizon-minor: [S0-D1, S0-D20, S0-D60, S1-D1, ...]
    d20 = [plan.case_ids[i * 3 + 1] for i in range(n_panel)]
    entry_date, exit_date, dates = await _window_dates(
        engine, exit_sessions_back=exit_sessions_back, span=20
    )
    for cid in d20:
        await _retarget_case(engine, cid, entry_date, exit_date)
        await _shift_case_window(engine, cid, (timedelta(hours=-1), timedelta(hours=1)))

    panel = ctx["panel"]
    bench = ctx["benchmark"]
    await _ingest_window(engine, "SPY", bench, dates, default={"open": 500.0, "close": 500.0})
    # exit closes -> asset returns: S0 +0.2, S1 -0.1, S2 +0.1, S3 +0.05
    closes = {i: c for i, c in {0: 120.0, 1: 90.0, 2: 110.0, 3: 105.0}.items() if i < n_panel}
    for i, close in closes.items():
        await _ingest_window(
            engine, f"S{i}", panel[i], dates, default={"open": 100.0, "close": close}
        )

    # commits
    await _seal(engine, tenant_id, d20[0], sources=_sources(baseline_p=0.6, quant_p=0.7, expected=0.1))
    if n_panel > 1:
        await _seal(
            engine, tenant_id, d20[1],
            sources=_sources(quant_status="unavailable", quant_reason="model_error", baseline_p=0.55),
        )
    # d20[2]: no commit
    if n_panel > 3:
        await _seal(engine, tenant_id, d20[3], sources=_sources(baseline_p=0.5, quant_p=0.6), confirm=False)
        await _shift_case_window(engine, d20[3], (timedelta(hours=-2), timedelta(hours=-1)))
        await _seal(engine, tenant_id, d20[3], sources=_sources(baseline_p=0.5, quant_p=0.6))  # confirms late
    if n_panel > 4:
        await _seal(engine, tenant_id, d20[4], sources=_sources(baseline_p=0.5, quant_p=0.6))
    if n_panel > 5:
        await _seal(engine, tenant_id, d20[5], sources=_sources(baseline_p=0.5, quant_p=0.6))

    # outcomes (S2 has no commit but its outcome still resolves —
    # the report needs a definite state for every planned case)
    for i in range(n_panel):
        await resolve_outcome(engine, d20[i])
    if n_panel > 5:
        await record_unscorable(
            engine, d20[5], reason="halted_at_entry", evidence_ref="exchange-notice-1"
        )
    return ctx, plan, d20


async def test_batch_report_primary_metric_and_coverage(db_engine, tenant_id):
    ctx, plan, d20 = await _scenario(db_engine, tenant_id)

    result = await generate_batch_report(db_engine, plan.batch_id, 20)
    assert result.created and result.report_version == 1
    content = result.content

    # fixed references
    assert content["horizon_td"] == 20
    assert content["horizon_role"] == "primary"
    assert content["release_id"] == "rel-test-v1"
    assert content["scoring_code_version"] == "scoring-v1"
    assert content["primary_metric"] == "d20_paired_brier_delta"
    assert len(content["cases"]) == 6
    assert {c["case_id"] for c in content["cases"]} == {str(c) for c in d20}

    # coverage: full planned denominator with every path visible
    cov = content["coverage"]
    assert cov["planned_cases"] == 6
    assert cov["with_commit"] == 5
    assert cov["on_time_commits"] == 4
    assert cov["pairable"] == 1
    assert cov["outcome_status"] == {"resolved": 4, "unresolved": 1, "unscorable": 1}
    assert cov["source_status"]["baseline"] == {
        "produced": 5, "fallback": 0, "unavailable": 0, "no_position": 1
    }
    assert cov["source_status"]["quant_model"] == {
        "produced": 4, "fallback": 0, "unavailable": 1, "no_position": 1
    }
    assert cov["source_status"]["llm_adjusted"] == {
        "produced": 0, "fallback": 0, "unavailable": 5, "no_position": 1
    }

    # primary metric: only S0 is pairable
    # d = (0.7-1)^2 - (0.6-1)^2 = 0.09 - 0.16 = -0.07
    m = content["metrics"]
    assert m["mean_paired_brier_delta"] == "-0.0700000000"
    assert m["paired_n"] == 1
    # per-source Brier over each source's own scorable set:
    # baseline: S0 (0.6, y=1) -> 0.16; S1 (0.55, y=0) -> 0.3025
    assert m["brier"]["baseline"] == "0.2312500000"
    assert m["brier_n"]["baseline"] == 2
    assert m["brier"]["quant_model"] == "0.0900000000"
    assert m["brier_n"]["quant_model"] == 1

    # per-case rows carry the exclusion reasons and fixed references
    by_case = {c["case_id"]: c for c in content["cases"]}
    s0 = by_case[str(d20[0])]
    assert s0["pairable"] is True and s0["exclusions"] == []
    assert s0["y"] == 1
    assert s0["d_i"] == "-0.0700000000"
    assert s0["commit_timeliness"] == "on_time"
    assert s0["sources"]["quant_model"]["p_outperform"] == "0.7000000000"
    assert s0["outcome"]["status"] == "resolved"
    assert s0["outcome"]["revision"] == 1

    s1 = by_case[str(d20[1])]
    assert not s1["pairable"]
    assert "quant_model_not_produced" in s1["exclusions"]
    assert s1["y"] == 0  # excess -0.1

    s2 = by_case[str(d20[2])]
    assert s2["exclusions"] == ["no_commit"]
    assert s2["commit_id"] is None
    assert s2["y"] == 1  # still scored-eligible denominator, reported

    s3 = by_case[str(d20[3])]
    assert "commit_late" in s3["exclusions"]

    s4 = by_case[str(d20[4])]
    assert "outcome_unresolved" in s4["exclusions"]

    s5 = by_case[str(d20[5])]
    assert "outcome_unscorable" in s5["exclusions"]

    # rmse via Decimal sqrt (irrational): compare against same operation
    rmse = Decimal(m["mse_expected_excess"]["baseline"]).sqrt().quantize(
        Decimal("0.0000000001")
    )
    assert m["rmse_expected_excess"]["baseline"] == f"{rmse:f}"
    assert m["mse_expected_excess"]["quant_model"] == "0.0100000000"
    assert m["rmse_expected_excess"]["quant_model"] == "0.1000000000"

    # stored content hash matches a canonical recompute
    from youwei_core.ledger.service import sha256_hex

    assert result.content_sha256 == sha256_hex(content)

    # event emitted
    async with db_engine.begin() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM events "
                    "WHERE event_type = 'evaluation.report_generated'"
                )
            )
        ).scalar_one()
    assert n == 1


async def test_report_na_when_no_pairable_cases(db_engine, tenant_id):
    """No pairable case -> NA (None), never a fabricated 0."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    case = plan.case_ids[1]  # D20
    entry_date, exit_date, dates = await _window_dates(
        db_engine, exit_sessions_back=10, span=20
    )
    await _retarget_case(db_engine, case, entry_date, exit_date)
    await _shift_case_window(db_engine, case, (timedelta(hours=-1), timedelta(hours=1)))
    await _ingest_window(db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})
    await _ingest_window(db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 110.0})
    await _seal(
        db_engine, tenant_id, case,
        sources=_sources(quant_status="unavailable", quant_reason="model_error"),
    )
    await resolve_outcome(db_engine, case)

    result = await generate_batch_report(db_engine, plan.batch_id, 20)
    m = result.content["metrics"]
    assert m["paired_n"] == 0
    assert m["mean_paired_brier_delta"] is None  # NA
    assert m["brier"]["baseline"] == "0.2500000000"  # (0.5-1)^2, y=1
    assert m["brier"]["quant_model"] is None


async def test_zero_excess_counts_as_false(db_engine, tenant_id):
    """target-spec §1: y = 1(excess > 0); exactly 0 is false."""
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    case = plan.case_ids[1]  # D20
    entry_date, exit_date, dates = await _window_dates(
        db_engine, exit_sessions_back=10, span=20
    )
    await _retarget_case(db_engine, case, entry_date, exit_date)
    await _shift_case_window(db_engine, case, (timedelta(hours=-1), timedelta(hours=1)))
    # asset return 0 == benchmark return 0 -> excess exactly 0
    await _ingest_window(db_engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})
    await _ingest_window(db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 100.0})
    await _seal(db_engine, tenant_id, case, sources=_sources(baseline_p=0.4, quant_p=0.3))
    await resolve_outcome(db_engine, case)

    result = await generate_batch_report(db_engine, plan.batch_id, 20)
    row = result.content["cases"][0]
    assert row["y"] == 0
    # d = (0.3-0)^2 - (0.4-0)^2 = 0.09 - 0.16 = -0.07
    assert row["d_i"] == "-0.0700000000"


async def test_report_not_ready_gates(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # exits are in the future
    with pytest.raises(NotReady, match="planned exit"):
        await generate_batch_report(db_engine, plan.batch_id, 20)

    # matured exit but a case has no outcome head yet
    case = plan.case_ids[1]
    entry_date, exit_date, dates = await _window_dates(
        db_engine, exit_sessions_back=10, span=20
    )
    await _retarget_case(db_engine, case, entry_date, exit_date)
    with pytest.raises(NotReady, match="outcome head"):
        await generate_batch_report(db_engine, plan.batch_id, 20)

    with pytest.raises(BatchNotFound):
        await generate_batch_report(db_engine, uuid.uuid4(), 20)


async def test_report_idempotent_and_correction_appends_version(db_engine, tenant_id):
    ctx, plan, d20 = await _scenario(db_engine, tenant_id, n_panel=1)
    # n_panel=1: only the S0 path exists (pairable, resolved +0.2)
    v1 = await generate_batch_report(db_engine, plan.batch_id, 20)
    assert v1.content["metrics"]["mean_paired_brier_delta"] == "-0.0700000000"

    again = await generate_batch_report(db_engine, plan.batch_id, 20)
    assert not again.created
    assert again.report_version == 1
    assert again.report_id == v1.report_id

    # a vendor correction flips the outcome: exit close 120 -> 90
    # (excess -0.1, y 1 -> 0) -> new outcome revision -> new report
    entry_date, exit_date, dates = await _window_dates(
        db_engine, exit_sessions_back=10, span=20
    )
    await _ingest_window(
        db_engine, "S0", ctx["panel"][0], dates, default={"open": 100.0, "close": 90.0}
    )
    revised = await resolve_outcome(db_engine, d20[0], correction_reason="vendor_close_correction")
    assert revised.revision == 2

    v2 = await generate_batch_report(db_engine, plan.batch_id, 20)
    assert v2.created and v2.report_version == 2
    # d = (0.7-0)^2 - (0.6-0)^2 = 0.49 - 0.36 = 0.13
    assert v2.content["metrics"]["mean_paired_brier_delta"] == "0.1300000000"
    assert v2.content["cases"][0]["outcome"]["revision"] == 2

    # the old report keeps its original references and numbers
    old = await latest_report(db_engine, plan.batch_id, 20)
    assert old["report_version"] == 2  # latest is v2
    async with db_engine.begin() as conn:
        v1_row = (
            await conn.execute(
                text(
                    "SELECT content, supersedes_report_id FROM evaluation_reports "
                    "WHERE report_version = 1"
                )
            )
        ).mappings().one()
    assert v1_row["content"]["metrics"]["mean_paired_brier_delta"] == "-0.0700000000"
    assert v1_row["content"]["cases"][0]["outcome"]["revision"] == 1
    assert v1_row["supersedes_report_id"] is None
    assert v2.content != v1_row["content"]


async def test_report_append_only(db_engine, tenant_id):
    ctx, plan, d20 = await _scenario(db_engine, tenant_id, n_panel=1)
    await generate_batch_report(db_engine, plan.batch_id, 20)
    with pytest.raises(Exception, match="append-only"):
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM evaluation_reports"))
