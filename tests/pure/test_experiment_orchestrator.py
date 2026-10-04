"""S08c-3 pure logic: the exploration loop's Controller orchestration.

The loop's DB touchpoints (register/accept/active-lease) are monkeypatched
here; the HTTP planes are exercised through a real ExperimentRunnerClient
over a mock transport. The DB-level integration (real register/accept
against PostgreSQL) is covered by the SG test run.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.experiment import (
    ExperimentComputationReceipt,
    ExperimentLimits,
    ExperimentRequest,
)
from youwei_contracts.research import ResearchProposal, ResearchReference
from youwei_contracts.research_capability import generate_research_keypair

import youwei_core.ledger.experiment_orchestrator as orch
from youwei_core.ledger.experiment_client import ExperimentRunnerClient
from youwei_core.ledger.experiment_orchestrator import (
    ExperimentWiring,
    make_experiment_fetcher,
)
from youwei_core.ledger.research_client import ResearchSigningKey

TENANT = uuid.uuid4()
RUN = uuid.uuid4()
JOB = uuid.uuid4()
ATTEMPT_ID = uuid.uuid4()
ATTEMPT_NO = 1
CASE = uuid.uuid4()
EV_SHA = "e" * 64
EXEC_CONFIG = "research-exec-2026-10"

_ROWS = [{"security_id": "s", "trade_date": "2026-10-09", "close": 100.0}]
_CANONICAL = json.dumps(_ROWS, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
SNAPSHOT = {
    "id": str(uuid.uuid4()),
    "manifest": {
        "kind": "daily_bars",
        "query": {"kind": "daily_bars", "as_of": "2026-10-10T10:00:00+00:00", "mode": "forward"},
        "content_sha256": hashlib.sha256(_CANONICAL.encode()).hexdigest(),
        "code_version": "daily-bars-snapshot-v1",
    },
    "content": _CANONICAL,
}


def _key() -> ResearchSigningKey:
    private, public = generate_research_keypair()
    from youwei_contracts.research_capability import public_key_thumbprint

    return ResearchSigningKey(
        kid=public_key_thumbprint(public),
        private_key_pem=private,
        exec_config_version=EXEC_CONFIG,
    )


def _ask() -> ExperimentRequest:
    return ExperimentRequest(
        question="vol ratio quantile",
        motivation="not in the lib",
        requested_shape="{q: float}",
    )


def _receipts(experiment_id, snapshot_sha):
    return [
        ExperimentComputationReceipt(
            experiment_invocation_id=experiment_id,
            computation_id=uuid.uuid4(),
            request_sha256="1" * 64,
            code_sha256="2" * 64,
            image="youwei-sandbox@sha256:fixed",
            snapshot_sha256=snapshot_sha,
            status="succeeded",
            duration_seconds=1.0,
        )
    ]


class FakeRunnerPlane:
    """Mock transport for the Runner's experiment control plane."""

    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/experiment-authorizations":
            return httpx.Response(200, json={"status": "registered"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "terminated"})
        if request.method == "GET" and request.url.path.endswith("/receipts"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "unscripted"})


class Harness:
    def __init__(self, monkeypatch, *, turn_outputs):
        self.key = _key()
        self.plane = FakeRunnerPlane()
        self.experiment_client = ExperimentRunnerClient(
            "http://runner.test",
            transport=httpx.MockTransport(self.plane.handler),
        )
        self.wiring = ExperimentWiring(
            engine=None,
            client=self.experiment_client,
            key=self.key,
            limits=ExperimentLimits(
                max_computations=2, max_concurrent=1,
                max_total_duration_seconds=600.0,
                max_artifact_bytes=1024 * 1024,
            ),
        )
        self.registered = []
        self.accepted = []
        self.turn_requests = []
        self.turn_outputs = list(turn_outputs)
        self.terminate_calls = []

        async def fake_register(engine, registration):
            self.registered.append(registration)
            return type("R", (), {"created": True})()

        async def fake_accept(engine, **kwargs):
            self.accepted.append(kwargs)
            return type("A", (), {"created": True})()

        async def fake_lease(engine, attempt_id, attempt_no):
            return datetime.now(UTC) + timedelta(minutes=5)

        async def fake_research(client, key, request, *, expiry_provider, **kw):
            self.turn_requests.append(request)
            output = self.turn_outputs.pop(0)
            if callable(output):
                output = output(self)
            if isinstance(output, ExperimentRequest):
                return type(
                    "Res", (), {"proposal": None, "experiment_request": output}
                )()
            return type("Res", (), {"proposal": output, "experiment_request": None})()

        async def fake_run_instance(client, key, binding, request, **kwargs):
            self.dispatched = request
            receipts = _receipts(
                binding.experiment_invocation_id,
                hashlib.sha256(SNAPSHOT["content"].encode()).hexdigest(),
            )
            self.instance_receipts = receipts
            from youwei_contracts.experiment import ExperimentInvocationResult
            from youwei_contracts.experiment import ExperimentResult

            return ExperimentInvocationResult(
                ok=True,
                result=ExperimentResult(findings="ratio at median"),
                exit_code=0,
                image_digest="sha256:x",
            )

        async def fake_receipts(*, key, binding, expiry_provider):
            return self.instance_receipts

        async def fake_terminate(*, key, binding, expiry_provider):
            self.terminate_calls.append(binding.experiment_invocation_id)

        monkeypatch.setattr(orch, "register_experiment", fake_register)
        monkeypatch.setattr(orch, "accept_experiment", fake_accept)
        monkeypatch.setattr(orch, "active_lease_expiry", fake_lease)
        monkeypatch.setattr(orch, "run_research_via_runner", fake_research)
        monkeypatch.setattr(orch, "run_experiment_instance", fake_run_instance)
        monkeypatch.setattr(self.experiment_client, "receipts", fake_receipts)
        monkeypatch.setattr(self.experiment_client, "terminate", fake_terminate)

    def fetcher(self):
        return make_experiment_fetcher(
            wiring=self.wiring,
            run_id=RUN,
            tenant_id=TENANT,
            job_id=JOB,
            attempt_id=ATTEMPT_ID,
            attempt_no=ATTEMPT_NO,
            snapshot=SNAPSHOT,
            batch_manifest={"batch": 1},
            research_client=None,
            research_config={"model": "glm-5.3"},
        )


def _proposal(cite_experiment=None):
    references = []
    if cite_experiment is not None:
        references.append(
            ResearchReference(
                kind="code",
                locator=(
                    f"experiment:{cite_experiment}/computations/"
                    f"{uuid.uuid4()}/artifacts/r.json"
                ),
                note="the vol ratio",
            )
        )
    return ResearchProposal(
        run_id=RUN,
        case_id=CASE,
        source_status="produced",
        p_outperform=0.6,
        expected_excess_return=0.01,
        references=references,
        model={"model_version": "glm-5.3", "provider": "custom"},
    )


def _case():
    return {
        "id": CASE,
        "security_id": uuid.uuid4(),
        "horizon_td": 20,
        "decision_cutoff_utc": datetime.now(UTC),
        "prediction_deadline_utc": datetime.now(UTC) + timedelta(hours=1),
        "entry_at_utc": datetime.now(UTC) + timedelta(days=1),
        "exit_at_utc": datetime.now(UTC) + timedelta(days=20),
        "target_spec_id": "excess-tr-d20-v1",
        "target_spec_sha256": "a" * 64,
        "benchmark_security_id": uuid.uuid4(),
    }


def _quant():
    return {
        "source": "quant_model",
        "source_status": "produced",
        "p_outperform": 0.55,
        "expected_excess_return": 0.01,
        "model_version": "quant-momentum-v0",
    }


class TestExplorationLoop:
    async def test_experiment_round_trip_then_proposal(self, monkeypatch):
        """Turn 1 asks for an experiment; the loop registers -> dispatches ->
        verifies -> accepts -> re-enters; turn 2's proposal is returned and
        its experiment citation resolves against the accepted outcome."""
        ask = _ask()

        def reentry_proposal(h):
            experiment_id = h.registered[0].experiment_invocation_id
            return _proposal(cite_experiment=experiment_id)

        harness = Harness(monkeypatch, turn_outputs=[ask, reentry_proposal])
        fetch = harness.fetcher()

        proposal = await fetch(_case(), [], _quant())
        assert proposal.source_status == "produced"
        # Core registration: bound to the claimed attempt + case + snapshot
        reg = harness.registered[0]
        assert reg.attempt_id == ATTEMPT_ID and reg.attempt_no == ATTEMPT_NO
        assert reg.case_id == CASE
        assert reg.snapshot_id == uuid.UUID(SNAPSHOT["id"])
        assert reg.question == "vol ratio quantile"
        # Runner registration happened (control plane) then terminate (close)
        assert ("POST", "/v1/experiment-authorizations") in harness.plane.calls
        assert harness.terminate_calls == [reg.experiment_invocation_id]
        # acceptance recorded the verified result + receipts
        assert harness.accepted[0]["attempt_no"] == ATTEMPT_NO
        # the re-entry request carried the accepted outcome as context
        assert len(harness.turn_requests) == 2
        assert len(harness.turn_requests[1].experiments) == 1
        assert (
            harness.turn_requests[1].experiments[0].experiment_invocation_id
            == reg.experiment_invocation_id
        )

    async def test_first_turn_proposal_skips_experiments(self, monkeypatch):
        harness = Harness(monkeypatch, turn_outputs=[_proposal()])
        fetch = harness.fetcher()
        proposal = await fetch(_case(), [], _quant())
        assert proposal.source_status == "produced"
        assert harness.registered == []
        assert harness.plane.calls == []

    async def test_instance_failure_terminates_and_propagates(self, monkeypatch):
        ask = _ask()
        harness = Harness(monkeypatch, turn_outputs=[ask])

        async def failing_instance(client, key, binding, request, **kwargs):
            raise orch.ExperimentRunnerError("experiment dispatch failed (500)")

        monkeypatch.setattr(orch, "run_experiment_instance", failing_instance)
        fetch = harness.fetcher()
        with pytest.raises(orch.ExperimentRunnerError):
            await fetch(_case(), [], _quant())
        # the experiment was terminated on the Runner before propagating
        assert harness.terminate_calls == [
            harness.registered[0].experiment_invocation_id
        ]
        assert harness.accepted == []

    async def test_cancellation_terminates_and_reraises(self, monkeypatch):
        ask = _ask()
        harness = Harness(monkeypatch, turn_outputs=[ask])

        async def cancelled_instance(client, key, binding, request, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr(orch, "run_experiment_instance", cancelled_instance)
        fetch = harness.fetcher()
        with pytest.raises(asyncio.CancelledError):
            await fetch(_case(), [], _quant())
        assert harness.terminate_calls == [
            harness.registered[0].experiment_invocation_id
        ]

    async def test_cap_reached_propagates_after_terminate(self, monkeypatch):
        ask = _ask()
        harness = Harness(monkeypatch, turn_outputs=[ask, ask, ask])

        from youwei_core.ledger.experiment_records import (
            ExperimentCapReachedError,
        )

        real_register = orch.register_experiment
        calls = {"n": 0}

        async def capping_register(engine, registration):
            calls["n"] += 1
            if calls["n"] > 2:
                raise ExperimentCapReachedError("case cap reached")
            return await real_register(engine, registration)

        monkeypatch.setattr(orch, "register_experiment", capping_register)
        fetch = harness.fetcher()
        with pytest.raises(ExperimentCapReachedError):
            await fetch(_case(), [], _quant())
        # two experiments ran; the third turn's ask hit the cap
        assert calls["n"] == 3
        assert len(harness.terminate_calls) == 2

    async def test_unknown_experiment_citation_rejected(self, monkeypatch):
        ask = _ask()

        def citing_proposal(h):
            return _proposal(cite_experiment=uuid.uuid4())

        harness = Harness(monkeypatch, turn_outputs=[ask, citing_proposal])
        fetch = harness.fetcher()
        with pytest.raises(Exception, match="not.*part of this case"):
            await fetch(_case(), [], _quant())
