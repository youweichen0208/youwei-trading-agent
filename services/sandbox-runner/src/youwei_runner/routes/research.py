import asyncio
from datetime import UTC, datetime
import uuid
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from youwei_contracts.agent_runtime import (
    ResearchInvocationEnvelope,
    ResearchInvocationStatus,
    invocation_digest,
)
from youwei_contracts.research_capability import (
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
)
from fastapi import APIRouter
from youwei_runner.state import RunnerState, ResearchRecord

def build_router(state: RunnerState) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/research-invocations")
    async def research_submit(request: Request):
        cap = state.authorize_research(request, SCOPE_RESEARCH_RUN)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > state.settings.max_request_bytes:
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
        if key in state.research_records:
            record = state.research_records[key]
            if record.view.request_sha256 != digest:
                raise HTTPException(409, "invocation already bound to another payload")
            record.expires_at = max(record.expires_at, cap.exp)
            return JSONResponse(record.view.model_dump(mode="json"))
        if sum(
            r.task is not None and not r.task.done() for r in state.research_records.values()
        ) >= state.settings.max_parallel:
            raise HTTPException(429, "research runner at capacity")
        if len(state.research_records) >= state.settings.max_records:
            for old_key, old in list(state.research_records.items()):
                if old.task is not None and old.task.done() and old.expires_at < datetime.now(UTC):
                    del state.research_records[old_key]
            if len(state.research_records) >= state.settings.max_records:
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
        state.research_records[key] = record
        record.task = asyncio.create_task(state.execute_research(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)


    @router.get("/v1/research-invocations/{invocation_id}")
    async def research_status(invocation_id: uuid.UUID, request: Request):
        record = state.find_research_record(invocation_id, request, SCOPE_RESEARCH_STATUS)
        return record.view


    @router.delete("/v1/research-invocations/{invocation_id}")
    async def research_cancel(invocation_id: uuid.UUID, request: Request):
        record = state.find_research_record(invocation_id, request, SCOPE_RESEARCH_CANCEL)
        if record.task is not None and record.view.status == "running":
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
            record.view.status = "cancelled"
        return record.view


    return router
