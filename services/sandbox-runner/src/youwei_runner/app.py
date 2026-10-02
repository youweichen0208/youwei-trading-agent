import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from youwei_contracts.capability import CapabilityError, verify_capability
from youwei_contracts.sandbox import SandboxRequest, ExecutionResult, ExecutionStatus, request_digest
from youwei_contracts.agent_runtime import (
    ResearchInvocationEnvelope,
    ResearchInvocationRequest,
    ResearchInvocationResult,
    ResearchInvocationStatus,
    invocation_digest,
)
from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentComputationRequest,
    ExperimentComputationStatus,
    ExperimentInvocationEnvelope,
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentInvocationStatus,
    experiment_invocation_digest,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    SCOPE_EXPERIMENT_ADMIN,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_RUN_STATUS,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
    ResearchCapabilityError,
    verify_research_token,
)

from youwei_runner.experiment_store import (
    ExperimentStore,
    ExperimentStoreError,
    code_sha256,
    request_sha256,
    snapshot_sha256,
)
from youwei_runner.settings import RunnerSettings


@dataclass
class Record:
    request: SandboxRequest
    view: ExecutionStatus
    expires_at: datetime
    weight: int = 0
    task: asyncio.Task | None = None


@dataclass
class ResearchRecord:
    request: ResearchInvocationRequest
    runtime_token: str
    view: ResearchInvocationStatus
    expires_at: datetime
    task: asyncio.Task | None = None


@dataclass
class ExperimentRecord:
    """In-memory handle for one running/finished experiment computation. The
    durable truth is the persistent receipt (ExperimentStore); this record
    exists only to hold the asyncio task and the latest receipt object."""

    request: ExperimentComputationRequest
    receipt: ExperimentComputationReceipt
    task: asyncio.Task | None = None


Executor = Callable[[SandboxRequest], Awaitable[ExecutionResult]]
ResearchExecutor = Callable[
    [ResearchInvocationRequest], Awaitable[ResearchInvocationResult]
]
ExperimentInstanceExecutor = Callable[
    [ExperimentInvocationRequest, str, str], Awaitable[ExperimentInvocationResult]
]


@dataclass
class ExperimentInvocationRecord:
    """In-memory handle for one running/finished experiment instance
    dispatch (the container turn). The durable experiment evidence lives in
    the persistent store (authorizations + computation receipts); this record
    only carries the live task and the latest status view."""

    request: ExperimentInvocationRequest
    runtime_token: str
    tool_token: str
    view: ExperimentInvocationStatus
    expires_at: datetime
    task: asyncio.Task | None = None


def create_app(
    settings: RunnerSettings,
    *,
    executor: Executor | None = None,
    research_executor: ResearchExecutor | None = None,
    experiment_instance_executor: ExperimentInstanceExecutor | None = None,
) -> FastAPI:
    records: dict[tuple, Record] = {}
    research_records: dict[uuid.UUID, ResearchRecord] = {}
    # S08 experiment surface: durable store (authorizations + receipts) when
    # configured; the dict below only tracks live asyncio tasks.
    experiment_store: ExperimentStore | None = (
        ExperimentStore(settings.experiment_store_dir)
        if settings.experiment_store_dir
        else None
    )
    if experiment_store is not None:
        # Opened at construction (not only in lifespan) so a NEW app instance
        # over the same directory reloads persisted state immediately — this
        # is exactly the restart path: receipts left running become
        # failed(interrupted) and stay idempotent.
        experiment_store.open()
    experiment_records: dict[tuple[uuid.UUID, uuid.UUID], ExperimentRecord] = {}
    experiment_invocation_records: dict[uuid.UUID, ExperimentInvocationRecord] = {}

    @asynccontextmanager
    async def lifespan(app):
        if executor is None:
            from youwei_runner.execution import remove_orphaned_containers
            await remove_orphaned_containers()
        try:
            yield
        finally:
            tasks = [r.task for r in records.values() if r.task is not None]
            tasks += [r.task for r in research_records.values() if r.task is not None]
            tasks += [r.task for r in experiment_records.values() if r.task is not None]
            tasks += [
                r.task for r in experiment_invocation_records.values()
                if r.task is not None
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    def prune():
        for key, record in list(records.items()):
            if record.task is not None and record.task.done() and record.expires_at < datetime.now(UTC):
                del records[key]

    def weight(value):
        # Budget UTF-8 serialized content conservatively for Python strings and
        # bookkeeping. Also bounded by the Runner container's hard memory limit.
        return 4 * len(value.model_dump_json().encode("utf-8"))

    def authorize(request: Request):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "runner capability required")
        try:
            return verify_capability(settings.secret, raw[7:])
        except (CapabilityError, ValueError, KeyError, TypeError):
            raise HTTPException(403, "invalid runner capability") from None

    def check_binding(cap, job, digest):
        if (cap.job_id != job.job_id or cap.attempt_no != job.attempt_no
                or cap.tenant_id != job.tenant_id
                or "sandbox:execute" not in cap.scopes
                or f"payload:{digest}" not in cap.scopes):
            raise HTTPException(403, "runner capability scope mismatch")

    async def execute(record):
        work = None
        try:
            if executor is None:
                from youwei_runner.execution import execute_request
                work = asyncio.create_task(execute_request(record.request, settings))
            else:
                work = asyncio.create_task(executor(record.request))
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            extra = weight(result)
            if sum(r.weight for r in records.values()) + extra > settings.max_cached_bytes:
                raise RuntimeError("runner result exceeds cache budget")
            record.weight += extra
            record.view.result = result
            record.view.status = "succeeded"
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    @app.post("/v1/executions")
    async def submit(request: Request):
        cap = authorize(request)
        prune()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "runner request too large")
        try:
            job = SandboxRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid sandbox-v1 request") from None
        digest = request_digest(job)
        check_binding(cap, job, digest)
        key = (job.job_id, job.attempt_no)
        if key in records:
            record = records[key]
            if record.view.request_sha256 != digest:
                raise HTTPException(409, "attempt already bound to another payload")
            record.expires_at = max(record.expires_at, cap.exp)
            return JSONResponse(record.view.model_dump(mode="json"))
        # A cancelled job holds its slot until container cleanup has finished.
        if sum(r.task is not None and not r.task.done() for r in records.values()) >= settings.max_parallel:
            raise HTTPException(429, "runner at capacity")
        request_weight = weight(job)
        if sum(r.weight for r in records.values()) + request_weight > settings.max_cached_bytes:
            raise HTTPException(429, "runner input exceeds cache budget")
        if len(records) >= settings.max_records:
            # Retain an idempotency record through its final lease. An expired
            # receipt may be evicted; Core owns durable retry/fencing semantics.
            for old_key, old in list(records.items()):
                if old.task is not None and old.task.done() and old.expires_at < datetime.now(UTC):
                    del records[old_key]
            if len(records) >= settings.max_records:
                raise HTTPException(503, "runner receipt capacity reached")
        record = Record(job, ExecutionStatus(job_id=job.job_id, attempt_no=job.attempt_no,
                                             request_sha256=digest, status="running"), cap.exp, request_weight)
        records[key] = record
        record.task = asyncio.create_task(execute(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)

    def find_record(job_id, attempt_no, request):
        cap = authorize(request)
        prune()
        if str(cap.job_id) != job_id or cap.attempt_no != attempt_no:
            raise HTTPException(403, "runner capability scope mismatch")
        record = records.get((cap.job_id, attempt_no))
        if record is None:
            raise HTTPException(404, "execution unavailable; recover through Core")
        check_binding(cap, record.request, record.view.request_sha256)
        record.expires_at = max(record.expires_at, cap.exp)
        return record

    @app.get("/v1/executions/{job_id}/{attempt_no}")
    async def status(job_id: str, attempt_no: int, request: Request):
        record = find_record(job_id, attempt_no, request)
        return record.view

    @app.delete("/v1/executions/{job_id}/{attempt_no}")
    async def cancel(job_id: str, attempt_no: int, request: Request):
        record = find_record(job_id, attempt_no, request)
        if record.task is not None and record.view.status == "running":
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
            record.view.status = "cancelled"
        return record.view

    # --- research invocations (S07m) ----------------------------------------
    # A separate controlled entry for the agent-runtime research container,
    # keyed by invocation_id (one turn per case) and authorized by Ed25519
    # (the Runner holds only public keys; it never signs). The legacy sandbox
    # HMAC link is unchanged.

    def authorize_research(request: Request, scope: str):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "research capability required")
        try:
            cap = verify_research_token(settings.agent_runtime_public_keys, raw[7:])
        except ResearchCapabilityError:
            raise HTTPException(403, "invalid research capability") from None
        if cap.aud != AUD_RUNNER_EXEC:
            raise HTTPException(403, "research capability has wrong audience")
        if scope not in cap.scopes:
            raise HTTPException(403, f"research capability missing {scope} scope")
        return cap

    async def execute_research(record: ResearchRecord):
        work = None
        try:
            if research_executor is None:
                from youwei_runner.research import execute_research_request
                from youwei_runner.research import build_research_config

                config = build_research_config(settings)
                # The app layer already authorized the request (aud=runner-exec);
                # the container grant (aud=runtime-research) is passed through so
                # the research container can verify it independently.
                work = asyncio.create_task(
                    execute_research_request(
                        record.request, config, capability_token=record.runtime_token
                    )
                )
            else:
                work = asyncio.create_task(research_executor(record.request))
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            record.view.result = result
            record.view.status = "succeeded"
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    @app.post("/v1/research-invocations")
    async def research_submit(request: Request):
        cap = authorize_research(request, SCOPE_RESEARCH_RUN)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "research request too large")
        try:
            envelope = ResearchInvocationEnvelope.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid agent-runtime-v1 envelope") from None
        req = envelope.request
        digest = invocation_digest(req)
        # Binding: the grant's invocation/tenant/case/evidence must match.
        if (
            cap.invocation_id != req.invocation_id
            or cap.tenant_id != req.tenant_id
            or cap.case_id != req.case_id
            or cap.evidence_sha256 != req.evidence_sha256
            or cap.exec_config_version != req.exec_config_version
        ):
            raise HTTPException(403, "research capability binding mismatch")
        key = req.invocation_id
        if key in research_records:
            record = research_records[key]
            if record.view.request_sha256 != digest:
                raise HTTPException(409, "invocation already bound to another payload")
            record.expires_at = max(record.expires_at, cap.exp)
            return JSONResponse(record.view.model_dump(mode="json"))
        if sum(
            r.task is not None and not r.task.done() for r in research_records.values()
        ) >= settings.max_parallel:
            raise HTTPException(429, "research runner at capacity")
        if len(research_records) >= settings.max_records:
            for old_key, old in list(research_records.items()):
                if old.task is not None and old.task.done() and old.expires_at < datetime.now(UTC):
                    del research_records[old_key]
            if len(research_records) >= settings.max_records:
                raise HTTPException(503, "research receipt capacity reached")
        record = ResearchRecord(
            req,
            envelope.runtime_token,
            ResearchInvocationStatus(
                invocation_id=req.invocation_id,
                request_sha256=digest,
                status="running",
            ),
            cap.exp,
        )
        research_records[key] = record
        record.task = asyncio.create_task(execute_research(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)

    def find_research_record(invocation_id, request, scope):
        cap = authorize_research(request, scope)
        if cap.invocation_id != invocation_id:
            raise HTTPException(403, "research capability scope mismatch")
        record = research_records.get(invocation_id)
        if record is None:
            raise HTTPException(404, "research invocation unavailable; recover through Core")
        record.expires_at = max(record.expires_at, cap.exp)
        return record

    @app.get("/v1/research-invocations/{invocation_id}")
    async def research_status(invocation_id: uuid.UUID, request: Request):
        record = find_research_record(invocation_id, request, SCOPE_RESEARCH_STATUS)
        return record.view

    @app.delete("/v1/research-invocations/{invocation_id}")
    async def research_cancel(invocation_id: uuid.UUID, request: Request):
        record = find_research_record(invocation_id, request, SCOPE_RESEARCH_CANCEL)
        if record.task is not None and record.view.status == "running":
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
            record.view.status = "cancelled"
        return record.view

    # --- experiment surface (S08) ---------------------------------------------
    # Control plane (aud=runner-exec, scope experiment:admin): the Controller
    # registers the experiment authorization (bindings + limits + the snapshot
    # the Runner injects), terminates it on parent cancel/lease loss/attempt
    # change, and reads the receipts (trusted execution evidence).
    # Tool plane (aud=runner-tools, per-operation scopes): the experiment
    # instance submits computations, polls status, and reads artifacts. The
    # two audiences are mutually exclusive (privilege tests pin this).

    def authorize_experiment_tool(request: Request, scope: str):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "experiment capability required")
        try:
            cap = verify_research_token(settings.agent_runtime_public_keys, raw[7:])
        except ResearchCapabilityError:
            raise HTTPException(403, "invalid experiment capability") from None
        if cap.aud != AUD_RUNNER_TOOLS:
            raise HTTPException(403, "experiment capability has wrong audience")
        if scope not in cap.scopes:
            raise HTTPException(403, f"experiment capability missing {scope} scope")
        return cap

    def check_experiment_binding(cap, auth: ExperimentAuthorization):
        if (cap.tenant_id != auth.tenant_id
                or cap.case_id != auth.case_id
                or cap.evidence_sha256 != auth.evidence_sha256
                or cap.exec_config_version != auth.exec_config_version):
            raise HTTPException(403, "experiment capability binding mismatch")

    def _require_experiment_store() -> ExperimentStore:
        if experiment_store is None:
            raise HTTPException(503, "experiment entry not configured")
        return experiment_store

    def _status_view(receipt: ExperimentComputationReceipt) -> ExperimentComputationStatus:
        return ExperimentComputationStatus(
            experiment_invocation_id=receipt.experiment_invocation_id,
            computation_id=receipt.computation_id,
            status=receipt.status,
            partial=receipt.partial,
            stdout_excerpt=receipt.stdout_excerpt,
            stderr_excerpt=receipt.stderr_excerpt,
            artifacts=receipt.artifacts,
            error=receipt.error,
            duration_seconds=receipt.duration_seconds,
        )

    @app.post("/v1/experiment-authorizations")
    async def experiment_register(request: Request):
        cap = authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = _require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "experiment authorization too large")
        try:
            auth = ExperimentAuthorization.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 authorization") from None
        if (cap.invocation_id != auth.experiment_invocation_id
                or cap.tenant_id != auth.tenant_id
                or cap.case_id != auth.case_id
                or cap.evidence_sha256 != auth.evidence_sha256
                or cap.exec_config_version != auth.exec_config_version):
            raise HTTPException(403, "experiment capability binding mismatch")
        try:
            store.register(auth)
        except ExperimentStoreError:
            raise HTTPException(
                409, "experiment already registered with different content"
            ) from None
        return {"status": "registered"}

    @app.delete("/v1/experiment-authorizations/{experiment_id}")
    async def experiment_terminate(experiment_id: uuid.UUID, request: Request):
        cap = authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = _require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        # D2 requirement 3: reject new computations AND clean up containers.
        store.terminate(experiment_id)
        cancelled = [
            record for key, record in experiment_records.items()
            if key[0] == experiment_id
            and record.task is not None and not record.task.done()
        ]
        for record in cancelled:
            record.task.cancel()
        if cancelled:
            await asyncio.gather(*(r.task for r in cancelled), return_exceptions=True)
        # A task cancelled before its first step never runs its own receipt
        # update — belt and suspenders: any receipt still running for this
        # experiment becomes cancelled here.
        for record in cancelled:
            if record.receipt.status == "running":
                record.receipt = record.receipt.model_copy(
                    update={"status": "cancelled", "partial": False}
                )
                store.save_receipt(record.receipt)
        # Termination also reaches the experiment INSTANCE dispatch (S08c):
        # a running instance container is cancelled too — its tool calls
        # would all be rejected from here on anyway.
        instance = experiment_invocation_records.get(experiment_id)
        if (instance is not None and instance.task is not None
                and not instance.task.done()):
            instance.task.cancel()
            await asyncio.gather(instance.task, return_exceptions=True)
            if instance.view.status == "running":
                instance.view.status = "cancelled"
        return {"status": "terminated"}

    @app.get("/v1/experiment-authorizations/{experiment_id}/receipts")
    async def experiment_receipts(experiment_id: uuid.UUID, request: Request):
        cap = authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = _require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        return [
            r.model_dump(mode="json")
            for r in store.receipts_for(experiment_id)
        ]

    async def execute_experiment(record: ExperimentRecord, auth: ExperimentAuthorization):
        """Run one computation in the sandbox executor with the Runner-injected
        snapshot; every transition lands in the persistent receipt."""
        req = record.request
        sandbox_request = SandboxRequest(
            job_id=auth.job_id, run_id=auth.run_id,
            attempt_id=auth.attempt_id, attempt_no=auth.attempt_no,
            tenant_id=auth.tenant_id,
            script=req.code, argv=req.argv, env=req.env,
            snapshot=auth.snapshot,
        )
        work = None
        started = time.monotonic()

        def finish(**updates):
            record.receipt = record.receipt.model_copy(update=updates)
            experiment_store.save_receipt(record.receipt)

        try:
            if executor is not None:
                work = asyncio.create_task(executor(sandbox_request))
            else:
                from youwei_runner.execution import execute_request
                work = asyncio.create_task(execute_request(sandbox_request, settings))
            result = await asyncio.wait_for(work, timeout=req.timeout_seconds)
            budget = (auth.limits.max_artifact_bytes
                      - experiment_store.artifact_bytes_used(req.experiment_invocation_id))
            if sum(a.size for a in result.artifacts) > budget:
                finish(status="failed", partial=False,
                       error="experiment artifact budget exceeded",
                       duration_seconds=time.monotonic() - started)
                return
            experiment_store.write_artifacts(
                req.experiment_invocation_id, req.computation_id, result.artifacts)
            manifests = [
                ArtifactManifest(path=a.path, extension=a.extension,
                                 size=a.size, sha256=a.sha256)
                for a in result.artifacts
            ]
            duration = time.monotonic() - started
            if result.exit_code == 0:
                finish(status="succeeded", partial=False, artifacts=manifests,
                       stdout_excerpt=result.stdout[:4000],
                       stderr_excerpt=result.stderr[:4000],
                       duration_seconds=duration)
            else:
                finish(status="failed", partial=bool(manifests), artifacts=manifests,
                       error=f"sandbox exited {result.exit_code}",
                       stdout_excerpt=result.stdout[:4000],
                       stderr_excerpt=result.stderr[:4000],
                       duration_seconds=duration)
        except asyncio.TimeoutError:
            finish(status="timeout", partial=False,
                   error=f"computation exceeded {req.timeout_seconds}s",
                   duration_seconds=time.monotonic() - started)
        except asyncio.CancelledError:
            finish(status="cancelled", partial=False,
                   duration_seconds=time.monotonic() - started)
        except Exception as exc:
            finish(status="failed", partial=False,
                   error=f"{type(exc).__name__}: {exc}"[:2000],
                   duration_seconds=time.monotonic() - started)
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    @app.post("/v1/experiment-computations")
    async def experiment_submit(request: Request):
        cap = authorize_experiment_tool(request, SCOPE_EXPERIMENT_SUBMIT)
        store = _require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "experiment computation too large")
        try:
            req = ExperimentComputationRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 computation request") from None
        if req.experiment_invocation_id != cap.invocation_id:
            raise HTTPException(403, "computation is for another experiment")
        auth = store.authorization(req.experiment_invocation_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        check_experiment_binding(cap, auth)
        if store.is_terminated(req.experiment_invocation_id):
            raise HTTPException(403, "experiment terminated")
        digest = request_sha256(req.model_dump(mode="json"))
        existing = store.receipt(req.experiment_invocation_id, req.computation_id)
        if existing is not None:
            if existing.request_sha256 != digest:
                raise HTTPException(
                    409, "computation id already bound to another payload"
                )
            return JSONResponse(_status_view(existing).model_dump(mode="json"))
        # D2 requirement 1: enforce the registered limits.
        if store.computation_count(req.experiment_invocation_id) >= auth.limits.max_computations:
            raise HTTPException(429, "experiment computation budget reached")
        if store.running_count(req.experiment_invocation_id) >= auth.limits.max_concurrent:
            raise HTTPException(429, "experiment concurrency limit reached")
        remaining = (auth.limits.max_total_duration_seconds
                     - store.used_duration(req.experiment_invocation_id))
        if req.timeout_seconds > remaining:
            raise HTTPException(
                429, "experiment duration budget insufficient for requested timeout"
            )
        if sum(r.task is not None and not r.task.done()
               for r in experiment_records.values()) >= settings.max_parallel:
            raise HTTPException(429, "runner at capacity")
        receipt = ExperimentComputationReceipt(
            experiment_invocation_id=req.experiment_invocation_id,
            computation_id=req.computation_id,
            request_sha256=digest,
            code_sha256=code_sha256(req.code),
            image=settings.image,
            snapshot_sha256=snapshot_sha256(auth.snapshot),
            status="running",
            duration_seconds=0.0,
        )
        store.save_receipt(receipt)
        record = ExperimentRecord(req, receipt)
        experiment_records[(req.experiment_invocation_id, req.computation_id)] = record
        record.task = asyncio.create_task(execute_experiment(record, auth))
        return JSONResponse(_status_view(receipt).model_dump(mode="json"), status_code=202)

    def _find_experiment_receipt(
        experiment_id: uuid.UUID, computation_id: uuid.UUID, request: Request, scope: str
    ):
        cap = authorize_experiment_tool(request, scope)
        store = _require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        auth = store.authorization(experiment_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        check_experiment_binding(cap, auth)
        receipt = store.receipt(experiment_id, computation_id)
        if receipt is None:
            raise HTTPException(404, "computation unknown")
        return store, receipt

    @app.get("/v1/experiment-computations/{experiment_id}/{computation_id}")
    async def experiment_status(
        experiment_id: uuid.UUID, computation_id: uuid.UUID, request: Request
    ):
        _, receipt = _find_experiment_receipt(
            experiment_id, computation_id, request, SCOPE_EXPERIMENT_STATUS
        )
        return _status_view(receipt)

    @app.get(
        "/v1/experiment-computations/{experiment_id}/{computation_id}/artifacts/{artifact_path:path}"
    )
    async def experiment_read(
        experiment_id: uuid.UUID,
        computation_id: uuid.UUID,
        artifact_path: str,
        request: Request,
    ):
        store, receipt = _find_experiment_receipt(
            experiment_id, computation_id, request, SCOPE_EXPERIMENT_READ
        )
        manifest = next(
            (a for a in receipt.artifacts if a.path == artifact_path), None
        )
        if manifest is None:
            raise HTTPException(404, "artifact not in receipt")
        try:
            artifact = store.read_artifact(experiment_id, computation_id, manifest)
        except ExperimentStoreError:
            raise HTTPException(410, "artifact unreadable (missing or hash mismatch)") from None
        return artifact

    # --- experiment instance dispatch (S08c-2) ------------------------------
    # The Controller dispatches ONE experiment instance per experiment
    # (keyed by experiment_invocation_id): a fixed agent-runtime container
    # whose tools are exactly {sandbox_submit, sandbox_status, artifact_read}
    # against the tool plane above. Dispatch authorization is aud=runner-exec
    # with scope experiment:run — a DIFFERENT scope from experiment:admin, so
    # a registration/termination grant cannot spawn instances and a dispatch
    # grant cannot touch the control plane.

    async def execute_experiment_instance(record: ExperimentInvocationRecord):
        work = None
        try:
            if experiment_instance_executor is not None:
                work = asyncio.create_task(
                    experiment_instance_executor(
                        record.request, record.runtime_token, record.tool_token
                    )
                )
            else:
                from youwei_runner.experiment_instance import (
                    build_experiment_run_config,
                    execute_experiment_invocation,
                )

                config = build_experiment_run_config(settings)
                work = asyncio.create_task(
                    execute_experiment_invocation(
                        record.request,
                        config,
                        runtime_token=record.runtime_token,
                        tool_token=record.tool_token,
                    )
                )
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            if result.ok and result.result is not None:
                record.view.result = result
                record.view.status = "succeeded"
            else:
                record.view.status = "failed"
                record.view.error = (result.error or "experiment instance failed")[:500]
                record.view.result = result
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    @app.post("/v1/experiment-invocations")
    async def experiment_invocation_submit(request: Request):
        cap = authorize_research(request, SCOPE_EXPERIMENT_RUN)
        store = _require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "experiment invocation too large")
        try:
            envelope = ExperimentInvocationEnvelope.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 envelope") from None
        req = envelope.request
        digest = experiment_invocation_digest(req)
        if (
            cap.invocation_id != req.experiment_invocation_id
            or cap.tenant_id != req.tenant_id
            or cap.run_id != req.run_id
            or cap.job_id != req.job_id
            or cap.attempt_no != req.attempt_no
            or cap.case_id != req.case_id
            or cap.evidence_sha256 != req.evidence_sha256
            or cap.exec_config_version != req.exec_config_version
        ):
            raise HTTPException(403, "experiment capability binding mismatch")
        # The experiment must be REGISTERED here before an instance may run:
        # the tool plane binds every computation to the registered
        # authorization (limits + the Runner-injected snapshot).
        auth = store.authorization(req.experiment_invocation_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        if store.is_terminated(req.experiment_invocation_id):
            raise HTTPException(403, "experiment terminated")
        if (
            auth.tenant_id != req.tenant_id
            or auth.run_id != req.run_id
            or auth.job_id != req.job_id
            or auth.attempt_no != req.attempt_no
            or auth.case_id != req.case_id
            or auth.evidence_sha256 != req.evidence_sha256
            or auth.exec_config_version != req.exec_config_version
        ):
            raise HTTPException(403, "experiment invocation does not match its registration")
        key = req.experiment_invocation_id
        existing = experiment_invocation_records.get(key)
        if existing is not None:
            if existing.view.request_sha256 != digest:
                raise HTTPException(409, "invocation already bound to another payload")
            existing.expires_at = max(existing.expires_at, cap.exp)
            return JSONResponse(existing.view.model_dump(mode="json"))
        running_instances = sum(
            r.task is not None and not r.task.done()
            for r in experiment_invocation_records.values()
        )
        if running_instances >= settings.max_parallel:
            raise HTTPException(429, "experiment runner at capacity")
        if len(experiment_invocation_records) >= settings.max_records:
            for old_key, old in list(experiment_invocation_records.items()):
                if (old.task is not None and old.task.done()
                        and old.expires_at < datetime.now(UTC)):
                    del experiment_invocation_records[old_key]
            if len(experiment_invocation_records) >= settings.max_records:
                raise HTTPException(503, "experiment invocation capacity reached")
        record = ExperimentInvocationRecord(
            req,
            envelope.runtime_token,
            envelope.tool_token,
            ExperimentInvocationStatus(
                invocation_id=req.experiment_invocation_id,
                request_sha256=digest,
                status="running",
            ),
            cap.exp,
        )
        experiment_invocation_records[key] = record
        record.task = asyncio.create_task(execute_experiment_instance(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)

    @app.get("/v1/experiment-invocations/{experiment_id}")
    async def experiment_invocation_status(
        experiment_id: uuid.UUID, request: Request
    ):
        cap = authorize_research(request, SCOPE_EXPERIMENT_RUN_STATUS)
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        record = experiment_invocation_records.get(experiment_id)
        if record is None:
            raise HTTPException(404, "experiment invocation unavailable; recover through Core")
        record.expires_at = max(record.expires_at, cap.exp)
        return record.view

    @app.get("/healthz")
    async def health():
        return {
            "status": "ok",
            "contract_version": "sandbox-v1",
            "research": "agent-runtime-v1",
            "experiment": "experiment-v1" if experiment_store is not None else None,
        }

    return app
