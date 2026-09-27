"""Minimal S02a API: submit/query/cancel runs, event cursor.

Tenant identity arrives via X-Tenant-Id for S02a only; real
authentication and capability tokens are S02b.
"""

import uuid
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import UUID4

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs import service as jobs_service
from youwei_core.jobs.service import (
    IdempotencyConflict,
    RunNotFound,
    RunSubmission,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="youwei-core", version="0.1.0")
    app.state.settings = settings
    app.state.engine = make_engine(
        settings.database_url,
        pool_size=settings.api_pool_size,
        max_overflow=settings.api_max_overflow,
    )

    TenantHeader = Annotated[uuid.UUID, Header(alias="X-Tenant-Id")]
    IdemHeader = Annotated[str, Header(alias="Idempotency-Key")]

    @app.post("/v1/runs", status_code=201)
    async def submit_run(
        body: RunSubmission,
        request: Request,
        x_tenant_id: TenantHeader,
        idempotency_key: IdemHeader,
    ):
        engine = request.app.state.engine
        try:
            result = await jobs_service.submit_run(
                engine, x_tenant_id, body, idempotency_key
            )
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_payload_mismatch",
                    "existing_run_id": str(exc),
                },
            ) from None
        view = await jobs_service.get_run_view(engine, x_tenant_id, result.run_id)
        # 201 on first creation; 200 on idempotent replay (the client
        # cannot distinguish a lost response from a retry otherwise).
        # Explicit JSONResponse: the decorator's status_code would win
        # for a plain dict return.
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=201 if result.created else 200, content=view)

    @app.get("/v1/runs/{run_id}")
    async def get_run(run_id: UUID4, request: Request, x_tenant_id: TenantHeader):
        try:
            return await jobs_service.get_run_view(
                request.app.state.engine, x_tenant_id, run_id
            )
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(run_id: UUID4, request: Request, x_tenant_id: TenantHeader):
        try:
            return await jobs_service.cancel_run(
                request.app.state.engine, x_tenant_id, run_id
            )
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/v1/runs/{run_id}/events")
    async def list_events(
        run_id: UUID4,
        request: Request,
        x_tenant_id: TenantHeader,
        after: Annotated[int, Query(ge=0)] = 0,
    ):
        try:
            return {
                "events": await jobs_service.list_events(
                    request.app.state.engine, x_tenant_id, run_id, after
                )
            }
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    return app
