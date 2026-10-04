"""S08c-3 DB level: the exploration loop against real PostgreSQL.

The loop's DB touchpoints (register_experiment / accept_experiment /
active_lease_expiry) run REAL here; the external boundaries (the Runner's
HTTP planes and the research container) are faked. This pins the fencing
and acceptance integration the pure tests mock out.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from youwei_contracts.experiment import (
    ExperimentInvocationResult,
    ExperimentLimits,
    ExperimentRequest,
    ExperimentResult,
)
from youwei_contracts.research import ResearchProposal
from youwei_contracts.research_capability import (
    generate_research_keypair,
    public_key_thumbprint,
)

import youwei_core.ledger.experiment_orchestrator as orch
from youwei_core.ledger.experiment_client import ExperimentRunnerClient
from youwei_core.ledger.experiment_orchestrator import (
    ExperimentOrchestrationError,
    ExperimentWiring,
    active_lease_expiry,
    make_experiment_fetcher,
)
from youwei_core.ledger.research_client import ResearchSigningKey

from test_experiment_records import (
    EXEC_CONFIG,
    _case_with_open_window,
    _claimed_job,
)


def _key() -> ResearchSigningKey:
    private, public = generate_research_keypair()
    return ResearchSigningKey(
        kid=public_key_thumbprint(public),
        private_key_pem=private,
        exec_config_version=EXEC_CONFIG,
    )


async def _forward_snapshot(engine):
    """Insert a complete forward-mode snapshot row and return the exact
    read_snapshot-shaped dict ({id, manifest, content}) for it — the DB
    row's content hash and the fetcher's snapshot stay identical, which is
    what acceptance verifies against the receipts."""
    from youwei_core.db.meta import snapshots as snapshots_t

    rows = [{"security_id": "x", "trade_date": "2026-10-09", "close": 100.0}]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_sha = hashlib.sha256(canonical.encode()).hexdigest()
    snap_id = uuid.uuid4()
    manifest = {
        "kind": "daily_bars",
        "query": {"kind": "daily_bars", "as_of": "2026-10-10T10:00:00+00:00", "mode": "forward"},
        "content_sha256": content_sha,
        "code_version": "daily-bars-snapshot-v1",
    }
    async with engine.begin() as conn:
        await conn.execute(
            snapshots_t.insert().values(
                id=snap_id,
                kind="daily_bars",
                query=manifest["query"],
                query_sha256=hashlib.sha256(json.dumps(manifest["query"], sort_keys=True).encode()).hexdigest(),
                as_of=datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
                mode="forward",
                manifest=manifest,
                content=canonical,
                content_sha256=content_sha,
            )
        )
    return {"id": str(snap_id), "manifest": manifest, "content": canonical}


@pytest_asyncio.fixture
async def loop_env(db_engine, tenant_id, monkeypatch):
    ctx, batch, case_id = await _case_with_open_window(db_engine, tenant_id)
    claimed = await _claimed_job(db_engine, tenant_id)
    snapshot = await _forward_snapshot(db_engine)

    key = _key()
    harness = {"turns": 0, "requests": [], "registered_ids": []}

    async def fake_research(client, key, request, *, expiry_provider, **kw):
        harness["turns"] += 1
        harness["requests"].append(request)
        if harness["turns"] == 1:
            return type("Res", (), {
                "proposal": None,
                "experiment_request": ExperimentRequest(
                    question="vol ratio quantile",
                    motivation="not in the lib",
                    requested_shape="{q: float}",
                ),
            })()
        return type("Res", (), {
            "proposal": ResearchProposal(
                run_id=request.run_id,
                case_id=request.case_id,
                source_status="produced",
                p_outperform=0.6,
                expected_excess_return=0.01,
                model={"model_version": "glm-5.3", "provider": "custom"},
            ),
            "experiment_request": None,
        })()

    monkeypatch.setattr(orch, "run_research_via_runner", fake_research)

    instance_state = {}

    async def fake_run_instance(client, key, binding, request, **kwargs):
        instance_state["binding"] = binding
        instance_state["request"] = request
        # the receipts the Runner would return: one succeeded computation on
        # the registered snapshot's content hash
        receipts = harness["receipts_factory"](binding.experiment_invocation_id)
        harness["instance_receipts"] = receipts
        return ExperimentInvocationResult(
            ok=True,
            result=ExperimentResult(findings="ratio at median"),
            exit_code=0,
            image_digest="sha256:x",
        )

    monkeypatch.setattr(orch, "run_experiment_instance", fake_run_instance)

    def receipts_factory(experiment_id):
        import youwei_contracts.experiment as exp

        return [
            exp.ExperimentComputationReceipt(
                experiment_invocation_id=experiment_id,
                computation_id=uuid.uuid4(),
                request_sha256="1" * 64,
                code_sha256="2" * 64,
                image="youwei-sandbox@sha256:fixed",
                snapshot_sha256=hashlib.sha256(
                    snapshot["content"].encode()
                ).hexdigest(),
                status="succeeded",
                duration_seconds=1.0,
            )
        ]

    harness["receipts_factory"] = receipts_factory

    # a real ExperimentRunnerClient over a mock transport: register/terminate
    # get scripted responses; receipts is monkeypatched (needs the binding)
    plane_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        plane_calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/experiment-authorizations":
            return httpx.Response(200, json={"status": "registered"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "terminated"})
        return httpx.Response(404, json={"detail": "unscripted"})

    client = ExperimentRunnerClient(
        "http://runner.test", transport=httpx.MockTransport(handler)
    )

    async def fake_receipts(*, key, binding, expiry_provider):
        return harness["instance_receipts"]

    async def fake_terminate(*, key, binding, expiry_provider):
        plane_calls.append(("DELETE", f"/v1/experiment-authorizations/{binding.experiment_invocation_id}"))

    monkeypatch.setattr(client, "receipts", fake_receipts)
    monkeypatch.setattr(client, "terminate", fake_terminate)

    wiring = ExperimentWiring(
        engine=db_engine,
        client=client,
        key=key,
        limits=ExperimentLimits(
            max_computations=2, max_concurrent=1,
            max_total_duration_seconds=600.0,
            max_artifact_bytes=1024 * 1024,
        ),
    )

    # capture registrations by wrapping register_experiment? No — run REAL.

    return {
        "engine": db_engine,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "claimed": claimed,
        "snapshot": snapshot,
        "wiring": wiring,
        "harness": harness,
        "plane_calls": plane_calls,
        "key": key,
    }


async def test_exploration_loop_registers_and_accepts_in_db(db_engine, loop_env):
    env = loop_env
    fetch = make_experiment_fetcher(
        wiring=env["wiring"],
        run_id=env["claimed"].run_id,
        tenant_id=env["claimed"].tenant_id,
        job_id=env["claimed"].job_id,
        attempt_id=env["claimed"].attempt_id,
        attempt_no=env["claimed"].attempt_no,
        snapshot=env["snapshot"],
        batch_manifest={"batch": 1},
        research_client=None,
        research_config={"model": "glm-5.3"},
    )
    case = {
        "id": env["case_id"],
        "security_id": uuid.uuid4(),
        "horizon_td": 20,
        "decision_cutoff_utc": datetime.now(UTC) - timedelta(hours=1),
        "prediction_deadline_utc": datetime.now(UTC) + timedelta(hours=1),
        "entry_at_utc": datetime.now(UTC) + timedelta(days=1),
        "exit_at_utc": datetime.now(UTC) + timedelta(days=20),
        "target_spec_id": "excess-tr-d20-v1",
        "target_spec_sha256": "a" * 64,
        "benchmark_security_id": uuid.uuid4(),
    }
    proposal = await fetch(case, [], {
        "source": "quant_model", "source_status": "produced",
        "p_outperform": 0.55, "expected_excess_return": 0.01,
        "model_version": "quant-momentum-v0",
    })
    assert proposal.source_status == "produced"

    # the experiment was registered AND accepted in the DB
    from youwei_core.ledger.experiment_records import load_experiment_outcome

    async with db_engine.begin() as conn:
        from sqlalchemy import text

        row = (await conn.execute(
            text("SELECT id FROM experiment_records")
        )).scalar_one()
    outcome = await load_experiment_outcome(db_engine, row)
    assert outcome is not None
    assert outcome.result.findings == "ratio at median"
    assert outcome.image == "youwei-sandbox@sha256:fixed"

    # the re-entry turn carried the accepted outcome as context
    assert len(env["harness"]["requests"]) == 2
    assert len(env["harness"]["requests"][1].experiments) == 1


async def test_active_lease_expiry_reads_live_lease(db_engine, loop_env):
    env = loop_env
    exp = await active_lease_expiry(
        db_engine, env["claimed"].attempt_id, env["claimed"].attempt_no
    )
    assert exp > datetime.now(UTC) - timedelta(seconds=1)

    # expire the lease: the provider must refuse to sign grants
    from sqlalchemy import text

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE attempts SET lease_expires_at = now() - interval '1 second', "
                "status = 'expired' WHERE id = :a"
            ),
            {"a": str(env["claimed"].attempt_id)},
        )
    with pytest.raises(ExperimentOrchestrationError):
        await active_lease_expiry(
            db_engine, env["claimed"].attempt_id, env["claimed"].attempt_no
        )
