import uuid
from typing import Annotated, Literal
from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4, BaseModel, Field
from fastapi import APIRouter
from youwei_core.api.dependencies import require_tenant

router = APIRouter()

class ExploratoryResearchRequest(BaseModel):
    """S12a: one exploratory research question. The horizon is the
    registered protocol set; the benchmark defaults to the active
    campaign's benchmark when not named."""

    ticker: str = Field(min_length=1, max_length=16)
    horizon_td: Literal[1, 20, 60]
    benchmark_ticker: str | None = Field(default=None, min_length=1, max_length=16)


@router.post("/v1/research", status_code=201)
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


@router.get("/v1/research")
async def list_exploratory(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(require_tenant)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    from youwei_core.ledger import exploratory

    return await exploratory.list_exploratory_research(
        request.app.state.engine, tenant_id, limit=limit
    )


@router.get("/v1/research/{research_id}")
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


@router.get("/v1/research/{research_id}/report")
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


@router.post("/v1/research/{research_id}/cancel")
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

