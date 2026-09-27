"""S05a: atomic forecast sealing.

Acceptance mapped from the plan (S05) and time-protocol §4:
- the three source positions seal atomically in one commit; Phase 1A
  fixes llm_adjusted at unavailable/not_enabled; an enabled source
  that failed seals unavailable + reason, never a fake probability
- the seal transaction locks the chain head, reads clock_timestamp()
  and re-checks the window: before cutoff -> rejected, past deadline
  -> rejected (a seal that waited on the lock past the deadline is
  rejected, not backdated)
- fencing: a stale attempt never writes business results
- idempotency: replaying the same payload returns the original
  commit; different payload for the same (case, release) conflicts
- durable confirmation appends with its own clock; a crash between
  seal and confirmation leaves the commit unconfirmed -> uncertain
  once past the deadline; replay reconciles with the current clock
  (late if the deadline passed) and never upgrades a confirmed
  judgment
- the hash chain links every commit; verify_chain detects tampering
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from youwei_core.jobs.service import RunSubmission, submit_run
from youwei_core.jobs.worker import claim_next_job
from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.ledger.service import GENESIS_HASH, plan_batch
from youwei_core.ledger.sealing import (
    CaseNotFound,
    ClockSkewExceeded,
    EarlySeal,
    FencedSeal,
    LateSeal,
    ReleaseMismatch,
    SealConflict,
    SealRequest,
    SourcePrediction,
    SourceValidationError,
    commit_status,
    seal_commit,
    verify_chain,
)
from test_ledger_campaign import _setup


# --- helpers ------------------------------------------------------------------


def _sources(
    *,
    baseline_p=0.5,
    quant_p=0.6,
    quant_status="produced",
    quant_reason=None,
    expected=0.01,
):
    return [
        SourcePrediction(
            source="baseline",
            source_status="produced",
            p_outperform=baseline_p,
            expected_excess_return=0.0,
            model_version="baseline-v0",
        ),
        SourcePrediction(
            source="quant_model",
            source_status=quant_status,
            reason=quant_reason,
            p_outperform=None if quant_status == "unavailable" else quant_p,
            expected_excess_return=(
                None if quant_status == "unavailable" else expected
            ),
            model_version="quant-v0",
        ),
        SourcePrediction(
            source="llm_adjusted",
            source_status="unavailable",
            reason="not_enabled",
        ),
    ]


async def _attempt(engine, tenant_id, *, kind="research.seal", max_attempts=1):
    """A live claimed attempt the Controller would seal from."""
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(
            kind=kind,
            total_budget_micros=0,
            jobs=[{"kind": kind, "payload": {}, "max_attempts": max_attempts}],
        ),
        idempotency_key=f"idem-{uuid.uuid4()}",
    )
    return await claim_next_job(engine, "test-worker")


async def _shift_case_window(engine, case_id, window):
    """Move a sealed case window relative to the database clock
    (test fixture via the ops escape hatch)."""
    async with engine.begin() as conn:
        db_now = (await conn.execute(text("SELECT now()"))).scalar_one()
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE forecast_cases SET decision_cutoff_utc = :c, "
                "prediction_deadline_utc = :d WHERE id = :id"
            ),
            {"c": db_now + window[0], "d": db_now + window[1], "id": str(case_id)},
        )


async def _sealable_case(engine, tenant_id, *, window=(timedelta(hours=-1), timedelta(hours=1))):
    """Setup through the real registration path, then shift one case's
    window around the database clock. Returns (context, case_id, plan)."""
    ctx = await _setup(engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    case_id = plan.case_ids[0]
    await _shift_case_window(engine, case_id, window)
    return ctx, case_id, plan


async def _seal(engine, tenant_id, case_id, release_id="rel-test-v1", sources=None, **kwargs):
    claimed = await _attempt(engine, tenant_id)
    request = SealRequest(
        case_id=case_id,
        release_id=release_id,
        sources=sources if sources is not None else _sources(),
        input_manifest={"code_version": "seal-test-v1"},
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
    )
    return await seal_commit(engine, request, **kwargs), claimed


# --- the core seal ----------------------------------------------------------------


async def test_seal_commit_atomic_three_positions(db_engine, tenant_id):
    ctx, case_id, _ = await _sealable_case(db_engine, tenant_id)
    result, claimed = await _seal(db_engine, tenant_id, case_id)

    assert result.created
    assert result.chain_seq == 1
    assert result.timeliness == "on_time"
    assert result.confirmed_at is not None
    assert result.sealed_at <= result.confirmed_at

    status = await commit_status(db_engine, result.commit_id)
    assert status["timeliness"] == "on_time"
    by_source = {p["source"]: p for p in status["predictions"]}
    assert set(by_source) == {"baseline", "quant_model", "llm_adjusted"}
    assert by_source["baseline"]["p_outperform"] == "0.5000000000"
    assert by_source["quant_model"]["p_outperform"] == "0.6000000000"
    assert by_source["llm_adjusted"]["source_status"] == "unavailable"
    assert by_source["llm_adjusted"]["reason"] == "not_enabled"
    assert by_source["llm_adjusted"]["p_outperform"] is None

    # chain + outbox events
    chain = await verify_chain(db_engine, f"campaign:{ctx['campaign'].campaign_id}")
    assert chain["ok"] and chain["commit_count"] == 1
    async with db_engine.begin() as conn:
        sealed_ev = (
            await conn.execute(
                text("SELECT count(*) FROM events WHERE event_type = 'ledger.commit_sealed'")
            )
        ).scalar_one()
        confirmed_ev = (
            await conn.execute(
                text("SELECT count(*) FROM events WHERE event_type = 'ledger.commit_confirmed'")
            )
        ).scalar_one()
        cev = (
            await conn.execute(
                text(
                    "SELECT payload FROM forecast_commit_events "
                    "WHERE commit_id = :c AND event_type = 'durable_confirmation'"
                ),
                {"c": str(result.commit_id)},
            )
        ).mappings().one()
    assert sealed_ev == 1 and confirmed_ev == 1
    assert cev.payload["timeliness"] == "on_time"


async def test_seal_allows_failed_enabled_source(db_engine, tenant_id):
    """baseline succeeds, quant failed: both positions seal in the same
    commit; quant is unavailable + reason, not a fake probability."""
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    result, _ = await _seal(
        db_engine,
        tenant_id,
        case_id,
        sources=_sources(quant_status="unavailable", quant_reason="model_error"),
    )
    status = await commit_status(db_engine, result.commit_id)
    by_source = {p["source"]: p for p in status["predictions"]}
    assert by_source["quant_model"]["source_status"] == "unavailable"
    assert by_source["quant_model"]["reason"] == "model_error"
    assert by_source["baseline"]["source_status"] == "produced"


async def test_seal_rejects_window_violations(db_engine, tenant_id):
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)

    # before the cutoff
    await _shift_case_window(
        db_engine, case_id, (timedelta(hours=1), timedelta(hours=2))
    )
    with pytest.raises(EarlySeal):
        await _seal(db_engine, tenant_id, case_id)

    # past the deadline
    await _shift_case_window(
        db_engine, case_id, (timedelta(hours=-2), timedelta(hours=-1))
    )
    with pytest.raises(LateSeal):
        await _seal(db_engine, tenant_id, case_id)


async def test_seal_rejected_when_lock_wait_crosses_deadline(db_engine, tenant_id):
    """A seal blocked on the chain lock re-checks the clock after
    acquiring it: past the deadline it is rejected, never backdated."""
    from sqlalchemy import select

    from youwei_core.db.meta import ledger_chains

    ctx, case_id, _ = await _sealable_case(
        db_engine, tenant_id, window=(timedelta(hours=-1), timedelta(seconds=1))
    )
    chain_id = f"campaign:{ctx['campaign'].campaign_id}"

    # a second engine holds the chain lock
    from youwei_core.db.engine import make_engine

    url = None
    # grab the URL from the running engine
    url = db_engine.url.render_as_string(hide_password=False)
    lock_engine = make_engine(url, pool_size=1)
    try:
        async with lock_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    select(ledger_chains).where(ledger_chains.c.chain_id == chain_id).with_for_update()
                )
                # the seal waits; the deadline (now+1s) passes meanwhile
                task = asyncio.create_task(_seal(db_engine, tenant_id, case_id))
                await asyncio.sleep(1.5)
            # lock released: the seal acquires it and must reject
            with pytest.raises(LateSeal):
                await task
    finally:
        await lock_engine.dispose()


async def test_seal_fenced_stale_attempt(db_engine, tenant_id):
    """A stale worker returning after the job moved to a newer
    attempt: fenced, nothing written."""
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    claimed = await _attempt(db_engine, tenant_id, max_attempts=2)
    # the attempt fails, the job requeues and a newer attempt exists
    from youwei_core.jobs.worker import fail_attempt

    await fail_attempt(db_engine, claimed.job_id, claimed.attempt_no, "retry")
    newer = await claim_next_job(db_engine, "test-worker")
    assert newer.attempt_no > claimed.attempt_no

    request = SealRequest(
        case_id=case_id,
        release_id="rel-test-v1",
        sources=_sources(),
        input_manifest={"code_version": "seal-test-v1"},
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,  # stale
    )
    with pytest.raises(FencedSeal):
        await seal_commit(db_engine, request)

    # nothing was written
    async with db_engine.begin() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM forecast_commits")
            )
        ).scalar_one()
    assert n == 0


async def test_seal_fenced_cross_tenant_attempt(db_engine, tenant_id):
    """An attempt from another tenant cannot seal this campaign's
    case: fenced before any business write."""
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    other_tenant = uuid.uuid4()
    with pytest.raises(FencedSeal):
        await _seal(db_engine, other_tenant, case_id)


async def test_seal_idempotent_replay_and_conflict(db_engine, tenant_id):
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    first, _ = await _seal(db_engine, tenant_id, case_id)

    replay, _ = await _seal(db_engine, tenant_id, case_id)
    assert not replay.created
    assert replay.commit_id == first.commit_id
    assert replay.chain_seq == first.chain_seq
    assert replay.timeliness == "on_time"  # never re-judged

    # different business content for the same (case, release) conflicts
    with pytest.raises(SealConflict):
        await _seal(db_engine, tenant_id, case_id, sources=_sources(quant_p=0.7))

    async with db_engine.begin() as conn:
        commits = (
            await conn.execute(text("SELECT count(*) FROM forecast_commits"))
        ).scalar_one()
        confirmations = (
            await conn.execute(
                text("SELECT count(*) FROM forecast_commit_events "
                     "WHERE event_type = 'durable_confirmation'")
            )
        ).scalar_one()
    assert commits == 1
    assert confirmations == 1


async def test_seal_confirmation_crash_window_and_reconciliation(db_engine, tenant_id):
    # crash between the seal transaction and the confirmation
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    first, _ = await _seal(db_engine, tenant_id, case_id, confirm=False)
    assert first.timeliness == "unconfirmed"

    status = await commit_status(db_engine, first.commit_id)
    assert status["timeliness"] == "unconfirmed"  # still inside the window

    # the deadline passes with no confirmation ever arriving
    from datetime import datetime as dt

    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE forecast_cases SET prediction_deadline_utc = :d WHERE id = :id"
            ),
            {"d": dt.now(UTC) - timedelta(minutes=5), "id": str(case_id)},
        )
    status = await commit_status(db_engine, first.commit_id)
    assert status["timeliness"] == "uncertain"  # conservative, never on_time

    # a replay reconciles the missing confirmation with the current
    # clock: late, and it stays late
    replay, _ = await _seal(db_engine, tenant_id, case_id)
    assert not replay.created
    assert replay.timeliness == "late"
    again, _ = await _seal(db_engine, tenant_id, case_id)
    assert again.timeliness == "late"  # no upgrade


async def test_seal_value_range_validation(db_engine, tenant_id):
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)

    bad_cases = [
        _sources(baseline_p=-0.1),          # p < 0
        _sources(baseline_p=1.5),           # p > 1
        _sources(baseline_p=float("nan")),  # non-finite
        _sources(expected=float("inf")),    # non-finite expected
        [  # produced without a probability
            SourcePrediction(source="baseline", source_status="produced",
                             p_outperform=None),
            SourcePrediction(source="quant_model", source_status="produced",
                             p_outperform=0.6),
            SourcePrediction(source="llm_adjusted", source_status="unavailable",
                             reason="not_enabled"),
        ],
        [  # unavailable carrying a fake probability
            SourcePrediction(source="baseline", source_status="produced",
                             p_outperform=0.5),
            SourcePrediction(source="quant_model", source_status="unavailable",
                             reason="x", p_outperform=0.5),
            SourcePrediction(source="llm_adjusted", source_status="unavailable",
                             reason="not_enabled"),
        ],
        [  # llm_adjusted produced in Phase 1A
            SourcePrediction(source="baseline", source_status="produced",
                             p_outperform=0.5),
            SourcePrediction(source="quant_model", source_status="produced",
                             p_outperform=0.6),
            SourcePrediction(source="llm_adjusted", source_status="produced",
                             p_outperform=0.7),
        ],
        [  # fallback under the no-fallback policy
            SourcePrediction(source="baseline", source_status="produced",
                             p_outperform=0.5),
            SourcePrediction(source="quant_model", source_status="fallback",
                             reason="from_quant", p_outperform=0.6),
            SourcePrediction(source="llm_adjusted", source_status="unavailable",
                             reason="not_enabled"),
        ],
        [  # missing position: partial submit
            SourcePrediction(source="baseline", source_status="produced",
                             p_outperform=0.5),
            SourcePrediction(source="quant_model", source_status="produced",
                             p_outperform=0.6),
        ],
    ]
    for sources in bad_cases:
        with pytest.raises(SourceValidationError):
            await _seal(db_engine, tenant_id, case_id, sources=sources)

    async with db_engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM forecast_commits"))
        ).scalar_one()
    assert n == 0


async def test_seal_rejects_wrong_release_and_unknown_case(db_engine, tenant_id):
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)
    with pytest.raises(ReleaseMismatch):
        await _seal(db_engine, tenant_id, case_id, release_id="rel-other-v1")

    with pytest.raises(CaseNotFound):
        await _seal(db_engine, tenant_id, uuid.uuid4())


async def test_seal_stops_on_clock_skew(db_engine, tenant_id, monkeypatch):
    _, case_id, _ = await _sealable_case(db_engine, tenant_id)

    async def fake_skew(engine):
        return 999.0

    import youwei_core.ledger.sealing as sealing_mod

    monkeypatch.setattr(sealing_mod, "clock_skew_seconds", fake_skew)
    with pytest.raises(ClockSkewExceeded):
        await _seal(db_engine, tenant_id, case_id)
    async with db_engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM forecast_commits"))
        ).scalar_one()
    assert n == 0


# --- chain -----------------------------------------------------------------------


async def test_chain_links_and_detects_tampering(db_engine, tenant_id):
    ctx, case_id, plan = await _sealable_case(db_engine, tenant_id)
    chain_id = f"campaign:{ctx['campaign'].campaign_id}"
    window = (timedelta(hours=-1), timedelta(hours=1))
    for cid in plan.case_ids[1:]:
        await _shift_case_window(db_engine, cid, window)
    r1, _ = await _seal(db_engine, tenant_id, plan.case_ids[0])
    r2, _ = await _seal(db_engine, tenant_id, plan.case_ids[1])
    r3, _ = await _seal(db_engine, tenant_id, plan.case_ids[2])

    assert (r1.chain_seq, r2.chain_seq, r3.chain_seq) == (1, 2, 3)

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT chain_seq, prev_hash, content_sha256 FROM forecast_commits "
                    "ORDER BY chain_seq"
                )
            )
        ).mappings().all()
    assert rows[0].prev_hash == GENESIS_HASH
    for prev, cur in zip(rows, rows[1:]):
        assert cur.prev_hash == prev.content_sha256

    chain = await verify_chain(db_engine, chain_id)
    assert chain["ok"] and chain["commit_count"] == 3

    # tamper with a sealed prediction via the ops escape hatch: the
    # recomputed content hash no longer matches
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text("UPDATE predictions SET p_outperform = 0.1234567890 WHERE source = 'quant_model'")
        )
    chain = await verify_chain(db_engine, chain_id)
    assert not chain["ok"]
    assert any("content hash mismatch" in i for i in chain["issues"])
