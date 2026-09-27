"""S02b API: bearer-key authentication (tenant + admin bootstrap),
run submission/query/cancel, event cursor.

Tenant identity comes from the API key (no more X-Tenant-Id). The
admin bootstrap key is settings-configured and manages tenants/keys
only. Capability-token issuance for jobs happens in the worker loop
(youwei_core.auth.capability)."""

import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import UUID4, BaseModel, Field

from youwei_core.auth.service import Principal, authenticate, create_api_key, create_tenant, revoke_api_key
from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs import service as jobs_service
from youwei_core.jobs.service import IdempotencyConflict, RunNotFound, RunSubmission

_bearer = HTTPBearer(auto_error=False)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="youwei-core", version="0.2.0")
    app.state.settings = settings
    app.state.engine = make_engine(
        settings.database_url,
        pool_size=settings.api_pool_size,
        max_overflow=settings.api_max_overflow,
    )

    # --- auth dependencies -------------------------------------------------

    async def require_principal(
        request: Request,
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Principal:
        if creds is None or not creds.credentials:
            raise HTTPException(status_code=401, detail="authentication required")
        token = creds.credentials
        admin_key = request.app.state.settings.admin_api_key
        if admin_key and token == admin_key:
            return Principal(tenant_id=None, role="admin", key_id=None)
        principal = await authenticate(request.app.state.engine, token)
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid or revoked credentials")
        return principal

    async def require_tenant(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> uuid.UUID:
        if principal.role != "tenant" or principal.tenant_id is None:
            raise HTTPException(status_code=403, detail="tenant credentials required")
        return principal.tenant_id

    async def require_admin(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if principal.role != "admin":
            raise HTTPException(status_code=403, detail="admin credentials required")
        return principal

    # --- run endpoints (tenant keys) --------------------------------------

    @app.post("/v1/runs", status_code=201)
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

    @app.get("/v1/runs/{run_id}")
    async def get_run(
        run_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    ):
        try:
            return await jobs_service.get_run_view(request.app.state.engine, tenant_id, run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    ):
        try:
            return await jobs_service.cancel_run(request.app.state.engine, tenant_id, run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/v1/runs/{run_id}/events")
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

    # --- admin endpoints (bootstrap key) ----------------------------------

    class TenantCreate(BaseModel):
        slug: str = Field(min_length=1, max_length=100)

    class ApiKeyCreate(BaseModel):
        tenant_id: UUID4
        name: str = Field(min_length=1, max_length=100)
        role: str = Field(default="tenant", pattern="^(tenant)$")

    @app.post("/v1/admin/tenants", status_code=201)
    async def admin_create_tenant(
        body: TenantCreate,
        request: Request,
        _admin: Annotated[Principal, Depends(require_admin)],
    ):
        tenant_id = await create_tenant(request.app.state.engine, body.slug)
        return {"id": str(tenant_id), "slug": body.slug}

    @app.post("/v1/admin/api-keys", status_code=201)
    async def admin_create_api_key(
        body: ApiKeyCreate,
        request: Request,
        _admin: Annotated[Principal, Depends(require_admin)],
    ):
        key_id, raw = await create_api_key(
            request.app.state.engine, body.tenant_id, body.name, role=body.role
        )
        # raw token is returned exactly once
        return {"id": str(key_id), "token": raw}

    @app.delete("/v1/admin/api-keys/{key_id}", status_code=200)
    async def admin_revoke_api_key(
        key_id: UUID4,
        request: Request,
        _admin: Annotated[Principal, Depends(require_admin)],
    ):
        ok = await revoke_api_key(request.app.state.engine, key_id)
        if not ok:
            raise HTTPException(status_code=404, detail="key not found or already revoked")
        return {"revoked": str(key_id)}

    return app
