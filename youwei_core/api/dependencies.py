"""Request-scoped tenant and administrator authentication."""
import uuid
from typing import Annotated
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from youwei_core.auth.service import Principal, authenticate
_bearer = HTTPBearer(auto_error=False)

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
