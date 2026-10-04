import uuid
from datetime import date as date_type
from typing import Annotated
from fastapi import Depends, HTTPException, Query, Request
from pydantic import UUID4
from fastapi import APIRouter
from youwei_core.api.dependencies import require_tenant

router = APIRouter()

@router.get("/v1/campaigns/{campaign_id}/status")
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


@router.get("/v1/campaigns/{campaign_id}/cases")
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


@router.get("/v1/campaigns/{campaign_id}/batches/{batch_id}/reports/{horizon_td}")
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


@router.get("/v1/campaigns/{campaign_id}/monthly-reports/{month}")
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
