import uuid
from typing import Annotated
from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4
from youwei_core.jobs import service as jobs_service
from youwei_core.jobs.service import IdempotencyConflict, RunNotFound, RunSubmission
from fastapi import APIRouter
from youwei_core.api.dependencies import require_tenant

router = APIRouter()

@router.post("/v1/runs", status_code=201)
async def submit_run(
    body: RunSubmission,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    engine = request.app.state.engine
    try:
        result = await jobs_service.submit_run(
            engine, tenant_id, body, idempotency_key
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "idempotency_payload_mismatch", "existing_run_id": str(exc)},
        ) from None
    view = await jobs_service.get_run_view(engine, tenant_id, result.run_id)
    # 201 on first creation; 200 on idempotent replay. Explicit
    # JSONResponse: the decorator's status_code would win otherwise.
    return JSONResponse(status_code=201 if result.created else 200, content=view)


@router.get("/v1/runs/{run_id}")
async def get_run(
    run_id: UUID4,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
):
    try:
        return await jobs_service.get_run_view(request.app.state.engine, tenant_id, run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="run not found") from None


@router.post("/v1/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID4,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
):
    try:
        return await jobs_service.cancel_run(request.app.state.engine, tenant_id, run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="run not found") from None


@router.get("/v1/runs/{run_id}/events")
async def list_events(
    run_id: UUID4,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    after: Annotated[int, Query(ge=0)] = 0,
):
    try:
        return {
            "events": await jobs_service.list_events(
                request.app.state.engine, tenant_id, run_id, after
            )
        }
    except RunNotFound:
        raise HTTPException(status_code=404, detail="run not found") from None

