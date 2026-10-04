import asyncio
from datetime import UTC, datetime
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from youwei_contracts.sandbox import SandboxRequest, ExecutionStatus, request_digest
from fastapi import APIRouter
from youwei_runner.state import RunnerState, Record

def build_router(state: RunnerState) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/executions")
    async def submit(request: Request):
        cap = state.authorize(request)
        state.prune()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > state.settings.max_request_bytes:
                raise HTTPException(413, "runner request too large")
        try:
            job = SandboxRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid sandbox-v1 request") from None
        digest = request_digest(job)
        state.check_binding(cap, job, digest)
        key = (job.job_id, job.attempt_no)
        if key in state.records:
            record = state.records[key]
            if record.view.request_sha256 != digest:
                raise HTTPException(409, "attempt already bound to another payload")
            record.expires_at = max(record.expires_at, cap.exp)
            return JSONResponse(record.view.model_dump(mode="json"))
        # A cancelled job holds its slot until container cleanup has finished.
        if sum(r.task is not None and not r.task.done() for r in state.records.values()) >= state.settings.max_parallel:
            raise HTTPException(429, "runner at capacity")
        request_weight = state.weight(job)
        if sum(r.weight for r in state.records.values()) + request_weight > state.settings.max_cached_bytes:
            raise HTTPException(429, "runner input exceeds cache budget")
        if len(state.records) >= state.settings.max_records:
            # Retain an idempotency record through its final lease. An expired
            # receipt may be evicted; Core owns durable retry/fencing semantics.
            for old_key, old in list(state.records.items()):
                if old.task is not None and old.task.done() and old.expires_at < datetime.now(UTC):
                    del state.records[old_key]
            if len(state.records) >= state.settings.max_records:
                raise HTTPException(503, "runner receipt capacity reached")
        record = Record(job, ExecutionStatus(job_id=job.job_id, attempt_no=job.attempt_no,
                                             request_sha256=digest, status="running"), cap.exp, request_weight)
        state.records[key] = record
        record.task = asyncio.create_task(state.execute(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)


    @router.get("/v1/executions/{job_id}/{attempt_no}")
    async def status(job_id: str, attempt_no: int, request: Request):
        record = state.find_record(job_id, attempt_no, request)
        return record.view


    @router.delete("/v1/executions/{job_id}/{attempt_no}")
    async def cancel(job_id: str, attempt_no: int, request: Request):
        record = state.find_record(job_id, attempt_no, request)
        if record.task is not None and record.view.status == "running":
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
            record.view.status = "cancelled"
        return record.view


    return router
