"""S08c-1 DB level: experiment registration, acceptance, and resolution.

The Core-side experiment record is the pre-dispatch authorization (D2
requirement 1) and the post-verification acceptance (requirement 4). These
tests exercise the fencing, cap, idempotency, and evidence checks against a
real PostgreSQL with the actual Alembic schema (append-only triggers
included). They need Docker and are skipped without it.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentComputationReceipt,
    ExperimentLimits,
    ExperimentResult,
    ExperimentResultComputation,
    experiment_artifact_locator,
)
from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.db.meta import snapshots as snapshots_t
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
from youwei_core.jobs.worker import claim_next_job
from youwei_core.ledger.experiment_records import (
    MAX_EXPERIMENTS_PER_CASE,
    ExperimentCapReachedError,
    ExperimentConflictError,
    ExperimentEvidenceError,
    ExperimentFencedError,
    ExperimentReferenceError,
    ExperimentRegistration,
    accept_experiment,
    load_experiment_outcome,
    register_experiment,
    resolve_experiment_artifact,
)
from youwei_core.ledger.service import plan_batch

from test_ledger_campaign import _campaign, _release, _security, _setup

EV_SHA = "e" * 64
EXEC_CONFIG = "research-exec-2026-10"


async def _snapshot(engine) -> uuid.UUID:
    content = json.dumps([{"security_id": "x", "close": 1.0}])
    snap_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            snapshots_t.insert().values(
                id=snap_id,
                kind="daily_bars",
                query={"security_ids": ["x"]},
                query_sha256=hashlib.sha256(b"q").hexdigest(),
                as_of=datetime.now(UTC),
                mode="forward",
                manifest={"content_sha256": hashlib.sha256(content.encode()).hexdigest()},
                content=content,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            )
        )
    return snap_id


async def _claimed_job(engine, tenant_id):
    await submit_run(
        engine,
        tenant_id,
        RunSubmission(
            kind="research.batch_predict",
            jobs=[
                JobSubmission(
                    kind="research.batch_predict",
                    payload={"x": 1},
                    max_attempts=3,
                )
            ],
        ),
        idempotency_key=f"exp-job-{uuid.uuid4()}",
    )
    claimed = await claim_next_job(engine, "exp-test-worker")
    assert claimed is not None
    assert claimed.tenant_id == tenant_id
    return claimed


def _registration(claimed, case_id, snapshot_id, experiment_id=None):
    return ExperimentRegistration(
        experiment_invocation_id=experiment_id or uuid.uuid4(),
        tenant_id=claimed.tenant_id,
        run_id=claimed.run_id,
        job_id=claimed.job_id,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        case_id=case_id,
        evidence_sha256=EV_SHA,
        exec_config_version=EXEC_CONFIG,
        question="rolling vol ratio quantile",
        motivation="benchmark not covered by quant lib",
        requested_shape="{ratio: float, quantile: float}",
        limits=ExperimentLimits(
            max_computations=2,
            max_concurrent=1,
            max_total_duration_seconds=600.0,
            max_artifact_bytes=1024 * 1024,
        ),
        snapshot_id=snapshot_id,
    )


def _receipts_for(experiment_id, snapshot_sha, computations):
    return [
        ExperimentComputationReceipt(
            experiment_invocation_id=experiment_id,
            computation_id=cid,
            request_sha256=hashlib.sha256(("req" + str(cid)).encode()).hexdigest(),
            code_sha256=hashlib.sha256(code.encode()).hexdigest(),
            image="youwei-sandbox@sha256:fixed",
            snapshot_sha256=snapshot_sha,
            status="succeeded",
            artifacts=[
                ArtifactManifest(
                    path="result.json",
                    extension=".json",
                    size=len(artifact),
                    sha256=hashlib.sha256(artifact.encode()).hexdigest(),
                )
            ],
            duration_seconds=1.0,
        )
        for cid, code, artifact in computations
    ]


def _result_for(computations):
    return ExperimentResult(
        findings="the ratio sits at its median",
        warnings=[],
        computations=[
            ExperimentResultComputation(
                computation_id=cid,
                code=code,
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                status="succeeded",
                artifacts=[
                    ArtifactManifest(
                        path="result.json",
                        extension=".json",
                        size=len(artifact),
                        sha256=hashlib.sha256(artifact.encode()).hexdigest(),
                    )
                ],
            )
            for cid, code, artifact in computations
        ],
    )


async def _other_tenant_case(engine, tenant_id):
    """A second tenant's campaign with DISTINCT tickers (the shared
    _setup uses fixed SPY/S0 names that would collide on
    security_identities)."""
    from test_ledger_campaign import _build_calendar, _campaign, _release
    from test_data_pit import _security

    await _build_calendar(engine)
    benchmark = await _security(engine, ticker="XSPY")
    panel = [await _security(engine, ticker="XS0")]
    await _release(engine)
    campaign = await _campaign(engine, tenant_id, benchmark, panel)
    cutoff = next_weekly_cutoff(datetime.now(UTC))
    batch = await plan_batch(
        engine, campaign.campaign_id, decision_cutoff=cutoff
    )
    return batch.case_ids[0]


async def _case_with_open_window(engine, tenant_id):
    ctx = await _setup(engine, tenant_id, n_panel=1, first_cutoff=None, batch_count=12)
    cutoff = next_weekly_cutoff(datetime.now(UTC))
    batch = await plan_batch(
        engine, ctx["campaign"].campaign_id, decision_cutoff=cutoff
    )
    return ctx, batch, batch.case_ids[0]


@pytest_asyncio.fixture
async def exp_env(db_engine, tenant_id):
    """Calendar/release/campaign/case + claimed job + frozen snapshot."""
    ctx, batch, case_id = await _case_with_open_window(db_engine, tenant_id)
    claimed = await _claimed_job(db_engine, tenant_id)
    snap_id = await _snapshot(db_engine)
    return {
        "ctx": ctx,
        "batch": batch,
        "case_id": case_id,
        "claimed": claimed,
        "snapshot_id": snap_id,
    }


# --- registration --------------------------------------------------------------


async def test_register_experiment_creates_fenced_row(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    result = await register_experiment(db_engine, reg)
    assert result.created
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT * FROM experiment_records WHERE id = :i"),
                {"i": str(reg.experiment_invocation_id)},
            )
        ).mappings().one()
    assert row.attempt_no == exp_env["claimed"].attempt_no
    assert row.snapshot_id == exp_env["snapshot_id"]
    assert row.limits["max_computations"] == 2


async def test_register_is_idempotent_same_content(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    first = await register_experiment(db_engine, reg)
    second = await register_experiment(db_engine, reg)
    assert first.created and not second.created


async def test_register_conflict_on_different_content(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    await register_experiment(db_engine, reg)
    changed = _registration(
        exp_env["claimed"],
        exp_env["case_id"],
        exp_env["snapshot_id"],
        experiment_id=reg.experiment_invocation_id,
    )
    object.__setattr__(
        changed, "question", "a different question entirely"
    )
    with pytest.raises(ExperimentConflictError):
        await register_experiment(db_engine, changed)


async def test_register_fenced_for_stale_attempt_no(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    stale = ExperimentRegistration(
        **{
            **reg.__dict__,
            "attempt_no": reg.attempt_no + 5,
        }
    )
    with pytest.raises(ExperimentFencedError):
        await register_experiment(db_engine, stale)


async def test_register_fenced_for_expired_lease(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE attempts SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :a"
            ),
            {"a": str(exp_env["claimed"].attempt_id)},
        )
    with pytest.raises(ExperimentFencedError):
        await register_experiment(db_engine, reg)


async def test_register_fenced_for_other_tenant(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    other = ExperimentRegistration(
        **{
            **reg.__dict__,
            "tenant_id": uuid.uuid4(),
        }
    )
    with pytest.raises(ExperimentFencedError):
        await register_experiment(db_engine, other)


async def test_register_rejects_unknown_snapshot(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], uuid.uuid4()
    )
    with pytest.raises(Exception, match="snapshot"):
        await register_experiment(db_engine, reg)


async def test_register_rejects_cross_tenant_case(db_engine, exp_env):
    # A case from a DIFFERENT tenant's campaign must not be registrable by
    # this tenant's attempt.
    other_tenant = uuid.uuid4()
    other_case = await _other_tenant_case(db_engine, other_tenant)
    reg = _registration(
        exp_env["claimed"], other_case, exp_env["snapshot_id"]
    )
    with pytest.raises(ExperimentFencedError):
        await register_experiment(db_engine, reg)


async def test_register_rejects_past_deadline(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL youwei.ledger_mutation = 'on'"))
        await conn.execute(
            text(
                "UPDATE forecast_cases SET prediction_deadline_utc = "
                "now() - interval '1 minute' WHERE id = :c"
            ),
            {"c": str(exp_env["case_id"])},
        )
    with pytest.raises(Exception, match="deadline"):
        await register_experiment(db_engine, reg)


async def test_register_caps_experiments_per_case(db_engine, exp_env):
    claimed = exp_env["claimed"]
    for _ in range(MAX_EXPERIMENTS_PER_CASE):
        reg = _registration(claimed, exp_env["case_id"], exp_env["snapshot_id"])
        await register_experiment(db_engine, reg)
    extra = _registration(claimed, exp_env["case_id"], exp_env["snapshot_id"])
    with pytest.raises(ExperimentCapReachedError):
        await register_experiment(db_engine, extra)


async def test_register_append_only(db_engine, exp_env):
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"]
    )
    await register_experiment(db_engine, reg)
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                text(
                    "UPDATE experiment_records SET question = 'x' "
                    "WHERE id = :i"
                ),
                {"i": str(reg.experiment_invocation_id)},
            )


# --- acceptance ------------------------------------------------------------------


def _acceptance_env(exp_env, experiment_id):
    """Register one experiment and build the matching result + receipts."""
    return experiment_id


async def _registered_experiment(db_engine, exp_env):
    experiment_id = uuid.uuid4()
    reg = _registration(
        exp_env["claimed"], exp_env["case_id"], exp_env["snapshot_id"],
        experiment_id=experiment_id,
    )
    await register_experiment(db_engine, reg)
    async with db_engine.begin() as conn:
        snapshot_sha = (
            await conn.execute(
                text("SELECT content_sha256 FROM snapshots WHERE id = :s"),
                {"s": str(exp_env["snapshot_id"])},
            )
        ).scalar_one()
    comp = uuid.uuid4()
    computations = [(comp, "print('ratio')", '{"ratio": 1.2}')]
    receipts = _receipts_for(experiment_id, snapshot_sha, computations)
    result = _result_for(computations)
    return experiment_id, receipts, result


async def test_accept_experiment_records_verified_outcome(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    claimed = exp_env["claimed"]
    acceptance = await accept_experiment(
        db_engine,
        experiment_invocation_id=experiment_id,
        result=result,
        receipts=receipts,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        tenant_id=claimed.tenant_id,
    )
    assert acceptance.created
    outcome = await load_experiment_outcome(db_engine, experiment_id)
    assert outcome is not None
    assert outcome.image == "youwei-sandbox@sha256:fixed"
    assert outcome.result.findings == result.findings


async def test_accept_is_idempotent_and_conflicts(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    claimed = exp_env["claimed"]
    kwargs = dict(
        experiment_invocation_id=experiment_id,
        result=result,
        receipts=receipts,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        tenant_id=claimed.tenant_id,
    )
    first = await accept_experiment(db_engine, **kwargs)
    second = await accept_experiment(db_engine, **kwargs)
    assert first.created and not second.created
    changed = result.model_copy(deep=True)
    changed.findings = "different findings"
    with pytest.raises(ExperimentConflictError):
        await accept_experiment(
            db_engine,
            **{**kwargs, "result": changed},
        )


async def test_accept_rejects_unregistered_experiment(db_engine, exp_env):
    claimed = exp_env["claimed"]
    comp = uuid.uuid4()
    computations = [(comp, "print(1)", "{}")]
    receipts = _receipts_for(uuid.uuid4(), "0" * 64, computations)
    result = _result_for(computations)
    with pytest.raises(Exception, match="not registered"):
        await accept_experiment(
            db_engine,
            experiment_invocation_id=uuid.uuid4(),
            result=result,
            receipts=receipts,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            tenant_id=claimed.tenant_id,
        )


async def test_accept_rejects_snapshot_hash_mismatch(db_engine, exp_env):
    experiment_id, _, result = await _registered_experiment(db_engine, exp_env)
    claimed = exp_env["claimed"]
    comp = result.computations[0].computation_id
    computations = [(comp, "print('ratio')", '{"ratio": 1.2}')]
    bad_receipts = _receipts_for(experiment_id, "f" * 64, computations)
    with pytest.raises(ExperimentEvidenceError, match="snapshot"):
        await accept_experiment(
            db_engine,
            experiment_invocation_id=experiment_id,
            result=result,
            receipts=bad_receipts,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            tenant_id=claimed.tenant_id,
        )


async def test_accept_fenced_for_other_attempt(db_engine, exp_env):
    """A newer attempt must not accept the old attempt's experiment — its
    registration is fenced, and so is its acceptance."""
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    # expire the registered attempt's lease; a fresh claim creates attempt 2
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE attempts SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :a"
            ),
            {"a": str(exp_env["claimed"].attempt_id)},
        )
    claimed2 = await claim_next_job(db_engine, "exp-test-worker-2")
    assert claimed2.attempt_no > exp_env["claimed"].attempt_no
    with pytest.raises(ExperimentFencedError):
        await accept_experiment(
            db_engine,
            experiment_invocation_id=experiment_id,
            result=result,
            receipts=receipts,
            attempt_id=claimed2.attempt_id,
            attempt_no=claimed2.attempt_no,
            tenant_id=claimed2.tenant_id,
        )


async def test_accept_outcome_append_only(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    claimed = exp_env["claimed"]
    await accept_experiment(
        db_engine,
        experiment_invocation_id=experiment_id,
        result=result,
        receipts=receipts,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        tenant_id=claimed.tenant_id,
    )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                text(
                    "DELETE FROM experiment_outcomes WHERE "
                    "experiment_invocation_id = :i"
                ),
                {"i": str(experiment_id)},
            )


# --- reference resolution ----------------------------------------------------------


async def test_resolve_artifact_after_acceptance(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    claimed = exp_env["claimed"]
    await accept_experiment(
        db_engine,
        experiment_invocation_id=experiment_id,
        result=result,
        receipts=receipts,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        tenant_id=claimed.tenant_id,
    )
    comp = result.computations[0].computation_id
    locator = experiment_artifact_locator(experiment_id, comp, "result.json")
    resolved = await resolve_experiment_artifact(db_engine, locator)
    assert resolved.manifest.path == "result.json"
    assert resolved.receipt.computation_id == comp


async def test_resolve_rejects_unaccepted_experiment(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    comp = result.computations[0].computation_id
    locator = experiment_artifact_locator(experiment_id, comp, "result.json")
    with pytest.raises(ExperimentReferenceError, match="no accepted outcome"):
        await resolve_experiment_artifact(db_engine, locator)


async def test_resolve_rejects_missing_artifact(db_engine, exp_env):
    experiment_id, receipts, result = await _registered_experiment(
        db_engine, exp_env
    )
    claimed = exp_env["claimed"]
    await accept_experiment(
        db_engine,
        experiment_invocation_id=experiment_id,
        result=result,
        receipts=receipts,
        attempt_id=claimed.attempt_id,
        attempt_no=claimed.attempt_no,
        tenant_id=claimed.tenant_id,
    )
    comp = result.computations[0].computation_id
    locator = experiment_artifact_locator(experiment_id, comp, "missing.json")
    with pytest.raises(ExperimentReferenceError):
        await resolve_experiment_artifact(db_engine, locator)
