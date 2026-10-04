"""Phase 1A code-identity guards for the release code exception (2026-10-03).

The approved release ``release-logistic-ridge-candidate-20261002-v1``
(content sha256 bdb8bbe0...) registers five code_files; the deployed Core
images (r4, and the staged post-fix build) differ from that record ONLY in
``youwei_core/ledger/pipeline.py`` — by the S08c Controller experiment
wiring (f43531e) and the S07o quant-into-research-input change (cbf5259),
both verified line-by-line as Phase 1B/exploration-path only. The owner
accepted the difference as a bounded compatibility exception
(docs/ops/s12-d2-enablement-prep.md, release_code_exceptions registry).

These tests are the executable side of that acceptance:

1. the Phase 1A llm provider is byte-identical to the approved release's
   version (hash constant recorded below — any future change to the Phase 1A
   path breaks this test and requires a new release or a new exception);
2. a Phase 1A (two-source) campaign NEVER invokes the LLM research link,
   even when the worker carries research wiring (as the post-batch-1
   rollout will) — the fetcher would fail loudly, and llm_adjusted must
   seal unavailable/not_enabled (not an error path).
"""

import hashlib
import inspect

import pytest
from types import SimpleNamespace

from youwei_core.ledger.pipeline import _phase1a_llm_adjusted

# sha256 of the _phase1a_llm_adjusted source block at the approved release
# state (git 56a8d81 = the release's pipeline.py). Computed 2026-10-03;
# identical at HEAD at exception-registration time.
RELEASE_PHASE1A_LLM_PROVIDER_SHA256 = (
    "82de38a92228220390da6e8dfb970a1166318ec94c8ff8663b0b8c6d96ebe4b7"
)


def test_phase1a_llm_provider_is_byte_identical_to_release():
    """The Phase 1A llm_adjusted provider must stay byte-identical to the
    approved release's code. If this fails, the release code exception no
    longer covers the deployed behavior: register a new release (or a new,
    explicitly accepted exception) before deploying."""
    src = inspect.getsource(_phase1a_llm_adjusted)
    actual = hashlib.sha256(src.encode()).hexdigest()
    assert actual == RELEASE_PHASE1A_LLM_PROVIDER_SHA256, (
        "the Phase 1A llm provider changed since the approved release "
        f"({actual}); the 2026-10-03 code exception no longer applies"
    )


async def test_phase1a_batch_never_invokes_research_wiring(db_engine, tenant_id):
    """A Phase 1A campaign must not call the LLM even with research wiring
    present on the worker. The wired client counts invocations and would
    raise; llm_adjusted must seal unavailable/not_enabled (the fixed Phase 1A
    position), never an unavailable-with-error produced by a fetch attempt."""
    from test_ledger_campaign import _setup
    from test_ledger_outcomes import _ingest_window
    from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
    from youwei_core.jobs.worker import claim_next_job, complete_attempt
    from youwei_core.ledger.pipeline import make_batch_predict_handler
    from test_ledger_pipeline import _open_batch
    from youwei_core.ledger.service import plan_batch
    from youwei_core.data.calendar import next_weekly_cutoff
    from datetime import UTC, datetime
    from sqlalchemy import text
    from test_ledger_outcomes import _window_dates

    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine, ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    _, _, dates = await _window_dates(db_engine, exit_sessions_back=1, span=25)
    await _ingest_window(db_engine, "S0", ctx["panel"][0], dates)
    await _ingest_window(
        db_engine, "SPY", ctx["benchmark"], dates,
        default={"open": 500.0, "close": 500.0},
    )
    await _open_batch(db_engine, plan.batch_id, plan.case_ids)

    calls = {"n": 0}

    class _BoomClient:
        async def request(self, *args, **kwargs):
            calls["n"] += 1
            raise AssertionError("Phase 1A must never reach the research link")

    runner_research = SimpleNamespace(
        client=_BoomClient(),
        key=None,
        research_config={"model": "glm-5.3"},
        experiment_client=None,
        experiment_limits=None,
    )

    await submit_run(
        db_engine, tenant_id,
        RunSubmission(
            kind="research.batch_predict",
            jobs=[JobSubmission(
                kind="research.batch_predict",
                payload={"batch_id": str(plan.batch_id), "release_id": "rel-test-v1"},
            )],
        ),
        idempotency_key=f"batch-predict:{plan.batch_id}",
    )
    claimed = await claim_next_job(db_engine, "test-worker")
    assert claimed is not None and claimed.kind == "research.batch_predict"
    handler = make_batch_predict_handler(db_engine, runner_research=runner_research)
    summary = await handler(claimed)
    await complete_attempt(db_engine, claimed.job_id, claimed.attempt_no, summary)

    assert summary["sealed"] == 3  # 1 security x 3 horizons
    assert summary["failed"] == []
    assert calls["n"] == 0, "Phase 1A batch invoked the research link"

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(text(
                "SELECT source, source_status, reason FROM predictions "
                "WHERE source = 'llm_adjusted'"
            ))
        ).mappings().all()
    assert len(rows) == 3
    for r in rows:
        assert r.source_status == "unavailable"
        assert r.reason == "not_enabled", (
            "llm_adjusted must be the fixed Phase 1A position, not an error "
            f"path (got reason={r.reason!r})"
        )


async def test_release_code_exception_registry_append_only(db_engine, tenant_id):
    """The exception registry is append-only (UPDATE/DELETE blocked without
    the ops escape hatch) and keyed per (release, deployed digest)."""
    from datetime import UTC, datetime
    from sqlalchemy import text
    from youwei_core.db.meta import release_code_exceptions
    from youwei_core.ledger.service import plan_batch
    from youwei_core.data.calendar import next_weekly_cutoff
    from test_ledger_campaign import _setup

    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    campaign_id = ctx["campaign"].campaign_id
    async with db_engine.begin() as conn:
        from youwei_core.db.meta import research_releases
        from sqlalchemy import select as _select
        release_row_id = (
            await conn.execute(
                _select(research_releases.c.id).where(
                    research_releases.c.release_id == "rel-test-v1"
                )
            )
        ).scalar_one()

    values = dict(
        release_row_id=release_row_id,
        campaign_id=campaign_id,
        deployed_image_digest="sha256:" + "a" * 64,
        code_files_actual={"youwei_core/ledger/pipeline.py": "0" * 64},
        diff_summary="test difference",
        verification={"tests": ["test_phase1a_code_identity"]},
        approver="human-owner",
        decision_basis="test basis",
    )
    async with db_engine.begin() as conn:
        await conn.execute(release_code_exceptions.insert().values(**values))
    # unique (release, digest) blocks a second registration
    with pytest.raises(Exception):
        async with db_engine.begin() as conn:
            await conn.execute(release_code_exceptions.insert().values(**values))
    # append-only: UPDATE and DELETE blocked without the escape hatch
    for stmt in (
        "UPDATE release_code_exceptions SET approver = 'x'",
        "DELETE FROM release_code_exceptions",
    ):
        with pytest.raises(Exception):
            async with db_engine.begin() as conn:
                await conn.execute(text(stmt))
    async with db_engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM release_code_exceptions"))).scalar_one()
        assert n == 1
        created = (await conn.execute(
            text("SELECT created_at FROM release_code_exceptions")
        )).scalar_one()
        assert created.tzinfo is not None and created <= datetime.now(UTC)
