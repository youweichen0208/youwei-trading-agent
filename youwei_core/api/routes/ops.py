from typing import Annotated
from fastapi import Depends, Request
from youwei_core.auth.service import Principal
from youwei_core.ops.service import ops_status
from fastapi import APIRouter
from youwei_core.api.dependencies import require_admin

router = APIRouter()

@router.get("/healthz")
async def healthz():
    """Liveness only, no DB dependency: a database blip must not get
    the API killed by a restart policy. Deep checks live in
    /v1/ops/status."""
    return {"status": "ok"}


@router.get("/v1/ops/status")
async def get_ops_status(
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
):
    """Deployment health snapshot + alerts (queue backlog, unpublished
    outbox events, unreaped expired leases, overdue runs, WAL archive
    staleness)."""
    return await ops_status(request.app.state.engine, request.app.state.settings)
