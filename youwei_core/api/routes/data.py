import uuid
from datetime import date as date_type, datetime
from typing import Annotated
from fastapi import Depends, HTTPException, Query, Request
from fastapi import APIRouter
from youwei_core.api.dependencies import require_tenant

router = APIRouter()

@router.get("/v1/data/daily-bars")
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
