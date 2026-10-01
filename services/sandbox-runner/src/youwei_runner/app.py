import asyncio
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
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
    ResearchCapabilityError,
    verify_research_token,
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


Executor = Callable[[SandboxRequest], Awaitable[ExecutionResult]]
ResearchExecutor = Callable[
    [ResearchInvocationRequest], Awaitable[ResearchInvocationResult]
]


def create_app(
    settings: RunnerSettings,
    *,
    executor: Executor | None = None,
    research_executor: ResearchExecutor | None = None,
) -> FastAPI:
    records: dict[tuple, Record] = {}
    research_records: dict[uuid.UUID, ResearchRecord] = {}

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

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "contract_version": "sandbox-v1", "research": "agent-runtime-v1"}

    return app
