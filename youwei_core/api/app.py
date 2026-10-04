"""S02b API: bearer-key authentication (tenant + admin bootstrap),
run submission/query/cancel, event cursor.

Tenant identity comes from the API key (no more X-Tenant-Id). The
admin bootstrap key is settings-configured and manages tenants/keys
only. Capability-token issuance for jobs happens in the worker loop
(youwei_core.auth.capability)."""

import uuid
from datetime import date as date_type, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import UUID4, BaseModel, Field

from youwei_core.auth.service import Principal, authenticate, create_api_key, create_tenant, revoke_api_key
from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs import service as jobs_service
from youwei_core.jobs.service import IdempotencyConflict, RunNotFound, RunSubmission
from youwei_core.ops.service import ops_status

class ExploratoryResearchRequest(BaseModel):
    """S12a: one exploratory research question. The horizon is the
    registered protocol set; the benchmark defaults to the active
    campaign's benchmark when not named."""

    ticker: str = Field(min_length=1, max_length=16)
    horizon_td: Literal[1, 20, 60]
    benchmark_ticker: str | None = Field(default=None, min_length=1, max_length=16)


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

    @app.get("/v1/data/daily-bars")
    async def get_daily_bars(
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        ticker: Annotated[str, Query(min_length=1, max_length=16)],
        start_date: date_type,
        end_date: date_type,
        as_of: datetime | None = None,
    ):
        from youwei_core.data.assistant import query_daily_bars
        from youwei_core.data.pit import InvalidQuery
        from youwei_core.data.securities import AmbiguousIdentifier, UnknownSecurity

        try:
            return await query_daily_bars(
                request.app.state.engine, ticker=ticker, start_date=start_date,
                end_date=end_date, as_of=as_of,
            )
        except UnknownSecurity:
            raise HTTPException(status_code=404, detail="security not found") from None
        except (InvalidQuery, AmbiguousIdentifier) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    # --- exploratory research (tenant keys, S12a) --------------------------

    @app.post("/v1/research", status_code=201)
    async def submit_exploratory_research(
        body: ExploratoryResearchRequest,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ):
        from youwei_core.ledger import exploratory

        try:
            result = await exploratory.submit_exploratory_research(
                request.app.state.engine,
                tenant_id,
                ticker=body.ticker,
                horizon_td=body.horizon_td,
                benchmark_ticker=body.benchmark_ticker,
                idempotency_key=idempotency_key,
            )
        except exploratory.IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "idempotency_payload_mismatch", "existing": str(exc)},
            ) from None
        except exploratory.ExploratoryResearchError as exc:
            raise HTTPException(status_code=422, detail={"error": str(exc)}) from None
        view = await exploratory.get_exploratory_research(
            request.app.state.engine, tenant_id, result.research_id
        )
        # 201 on first creation; 200 on idempotent replay
        return JSONResponse(status_code=201 if result.created else 200, content=view)

    @app.get("/v1/research")
    async def list_exploratory(
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        from youwei_core.ledger import exploratory

        return await exploratory.list_exploratory_research(
            request.app.state.engine, tenant_id, limit=limit
        )

    @app.get("/v1/research/{research_id}")
    async def get_exploratory(
        research_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    ):
        from youwei_core.ledger import exploratory

        view = await exploratory.get_exploratory_research(
            request.app.state.engine, tenant_id, research_id
        )
        if view is None:
            raise HTTPException(status_code=404, detail="research not found")
        return view

    @app.get("/v1/research/{research_id}/report")
    async def get_exploratory_report(
        research_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        version: Annotated[int | None, Query(ge=1)] = None,
    ):
        from youwei_core.ledger import exploratory

        report = await exploratory.get_exploratory_report(
            request.app.state.engine, tenant_id, research_id, version=version
        )
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return report

    @app.post("/v1/research/{research_id}/cancel")
    async def cancel_exploratory(
        research_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    ):
        from youwei_core.ledger import exploratory

        try:
            return await exploratory.cancel_exploratory_research(
                request.app.state.engine, tenant_id, research_id
            )
        except exploratory.ExploratoryNotFound:
            raise HTTPException(status_code=404, detail="research not found") from None

    # --- campaign status (tenant keys, read-only) --------------------------

    @app.get("/v1/campaigns/{campaign_id}/status")
    async def get_campaign_status(
        campaign_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    ):
        from youwei_core.ledger.status import campaign_status

        view = await campaign_status(request.app.state.engine, campaign_id)
        if view is None or view["tenant_id"] != str(tenant_id):
            # do not leak the existence of other tenants' campaigns
            raise HTTPException(status_code=404, detail="campaign not found")
        return view

    # --- case listing (S10 slice 2: dashboard, read-only) -------------------

    @app.get("/v1/campaigns/{campaign_id}/cases")
    async def list_campaign_cases(
        campaign_id: UUID4,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        batch_id: Annotated[UUID4 | None, Query()] = None,
        horizon_td: Annotated[int | None, Query()] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        from youwei_core.ledger.status import campaign_cases_view

        view = await campaign_cases_view(
            request.app.state.engine,
            campaign_id,
            batch_id=batch_id,
            horizon_td=horizon_td,
            page=page,
            page_size=page_size,
        )
        if view is None or view["tenant_id"] != str(tenant_id):
            raise HTTPException(status_code=404, detail="campaign not found")
        return view

    # --- saved report reads (S10 slice 1: dashboard, read-only) -------------

    @app.get("/v1/campaigns/{campaign_id}/batches/{batch_id}/reports/{horizon_td}")
    async def get_batch_report(
        campaign_id: UUID4,
        batch_id: UUID4,
        horizon_td: int,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        version: Annotated[int | None, Query()] = None,
    ):
        from youwei_core.ledger.status import batch_report_view

        # Literal[int] path params match strictly against the raw path
        # string in pydantic v2, so validate the int explicitly
        if horizon_td not in (1, 20, 60):
            raise HTTPException(
                status_code=422, detail="horizon_td must be one of 1, 20, 60"
            )
        view = await batch_report_view(
            request.app.state.engine, campaign_id, batch_id, horizon_td, version
        )
        if view is None or view["tenant_id"] != str(tenant_id):
            raise HTTPException(status_code=404, detail="report not found")
        return view

    @app.get("/v1/campaigns/{campaign_id}/monthly-reports/{month}")
    async def get_monthly_report(
        campaign_id: UUID4,
        month: date_type,
        request: Request,
        tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
        version: Annotated[int | None, Query()] = None,
    ):
        from youwei_core.ledger.status import monthly_report_view

        view = await monthly_report_view(
            request.app.state.engine, campaign_id, month, version
        )
        if view is None or view["tenant_id"] != str(tenant_id):
            raise HTTPException(status_code=404, detail="report not found")
        return view

    # --- ops endpoints (health & alerting) --------------------------------

    @app.get("/healthz")
    async def healthz():
        """Liveness only, no DB dependency: a database blip must not get
        the API killed by a restart policy. Deep checks live in
        /v1/ops/status."""
        return {"status": "ok"}

    @app.get("/v1/ops/status")
    async def get_ops_status(
        request: Request,
        _admin: Annotated[Principal, Depends(require_admin)],
    ):
        """Deployment health snapshot + alerts (queue backlog, unpublished
        outbox events, unreaped expired leases, overdue runs, WAL archive
        staleness)."""
        return await ops_status(request.app.state.engine, request.app.state.settings)

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
