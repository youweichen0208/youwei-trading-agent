"""S02b: API key authentication.

Bearer API keys map principals to tenants (sha256-hashed at rest, raw
token shown once). Admin bootstrap key comes from settings and manages
tenants/keys. The S02a X-Tenant-Id shortcut is gone.
"""

import uuid

from youwei_core.auth.service import create_api_key, create_tenant, revoke_api_key


async def test_no_credentials_rejected(client):
    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
    )
    assert r.status_code == 401


async def test_invalid_token_rejected(client):
    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={"Authorization": "Bearer ywa_bogus", "Idempotency-Key": "auth-1"},
    )
    assert r.status_code == 401


async def test_x_tenant_id_header_no_longer_grants_access(client):
    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={"X-Tenant-Id": str(uuid.uuid4()), "Idempotency-Key": "auth-2"},
    )
    assert r.status_code == 401


async def test_valid_tenant_key_submits_run(client, tenant_headers):
    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={**tenant_headers, "Idempotency-Key": "auth-3"},
    )
    assert r.status_code == 201


async def test_revoked_key_rejected(client, db_engine, tenant_headers):
    # find the key we just used via its prefix and revoke it
    token = tenant_headers["Authorization"].removeprefix("Bearer ")
    prefix = token[:12]
    from sqlalchemy import select

    from youwei_core.db.meta import api_keys

    async with db_engine.begin() as conn:
        key_id = (
            await conn.execute(
                select(api_keys.c.id).where(api_keys.c.token_prefix == prefix)
            )
        ).scalar_one()
    await revoke_api_key(db_engine, key_id)

    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={**tenant_headers, "Idempotency-Key": "auth-4"},
    )
    assert r.status_code == 401


async def test_admin_bootstrap_manages_tenants_and_keys(client, admin_headers, db_engine):
    # create a tenant
    r = await client.post(
        "/v1/admin/tenants", json={"slug": "acme"}, headers=admin_headers
    )
    assert r.status_code == 201
    tenant_id = r.json()["id"]

    # issue an api key for it
    r2 = await client.post(
        "/v1/admin/api-keys",
        json={"tenant_id": tenant_id, "name": "ci"},
        headers=admin_headers,
    )
    assert r2.status_code == 201
    raw = r2.json()["token"]
    assert raw.startswith("ywa_")

    # the issued key can submit runs
    r3 = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={"Authorization": f"Bearer {raw}", "Idempotency-Key": "auth-5"},
    )
    assert r3.status_code == 201


async def test_tenant_key_cannot_use_admin_endpoints(client, tenant_headers):
    r = await client.post(
        "/v1/admin/tenants", json={"slug": "evil"}, headers=tenant_headers
    )
    assert r.status_code == 403


async def test_admin_key_cannot_submit_runs_directly(client, admin_headers):
    """Admin keys manage keys/tenants only; runs need a tenant key."""
    r = await client.post(
        "/v1/runs",
        json={"kind": "research", "total_budget_micros": 1000, "jobs": [{"kind": "noop"}]},
        headers={**admin_headers, "Idempotency-Key": "auth-6"},
    )
    assert r.status_code == 403
