from typing import Annotated
from fastapi import Depends, HTTPException, Request
from pydantic import UUID4, BaseModel, Field
from youwei_core.auth.service import Principal, create_api_key, create_tenant, revoke_api_key
from fastapi import APIRouter
from youwei_core.api.dependencies import require_admin

router = APIRouter()

class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=100)


class ApiKeyCreate(BaseModel):
    tenant_id: UUID4
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="tenant", pattern="^(tenant)$")


@router.post("/v1/admin/tenants", status_code=201)
async def admin_create_tenant(
    body: TenantCreate,
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
):
    tenant_id = await create_tenant(request.app.state.engine, body.slug)
    return {"id": str(tenant_id), "slug": body.slug}


@router.post("/v1/admin/api-keys", status_code=201)
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


@router.delete("/v1/admin/api-keys/{key_id}", status_code=200)
async def admin_revoke_api_key(
    key_id: UUID4,
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
):
    ok = await revoke_api_key(request.app.state.engine, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="key not found or already revoked")
    return {"revoked": str(key_id)}

