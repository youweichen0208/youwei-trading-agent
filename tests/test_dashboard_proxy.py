"""S10 slice 3: the dashboard read-only proxy.

Browser -> HTTPS Basic Auth -> this same-origin proxy -> Core. The
tenant key lives ONLY in the proxy process env: the browser never
sees it (no page, no URL, no storage), and only the explicitly
listed dashboard GET paths are forwarded — everything else, including
every Core write path, is a 404 before it can reach Core.
"""

import base64
import hashlib
import hmac
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "dashboard"))

from proxy import create_app  # noqa: E402


class FakeCore:
    """Records what the proxy actually forwarded."""

    def __init__(self):
        from fastapi import FastAPI, Request

        seen: list = []
        self.seen = seen
        app = FastAPI()

        @app.api_route(
            "/v1/campaigns/{campaign_id}/status", methods=["GET"]
        )
        @app.api_route(
            "/v1/campaigns/{campaign_id}/cases", methods=["GET"]
        )
        @app.api_route(
            "/v1/campaigns/{campaign_id}/batches/{batch_id}/reports/{horizon}",
            methods=["GET"],
        )
        @app.api_route(
            "/v1/campaigns/{campaign_id}/monthly-reports/{month}",
            methods=["GET"],
        )
        @app.api_route("/v1/research", methods=["GET"])
        @app.api_route("/v1/research/{research_id}", methods=["GET"])
        @app.api_route("/v1/research/{research_id}/report", methods=["GET"])
        async def read(request: Request):
            seen.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query),
                    "authorization": request.headers.get("authorization"),
                }
            )
            return {"core": True, "path": request.url.path}

        @app.post("/v1/runs")
        async def write():
            seen.append({"method": "POST", "path": "/v1/runs"})
            return {"core": True, "write": True}

        self.app = app


def _hash_password(password: str) -> str:
    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt:{salt.hex()}:{digest.hex()}"


@pytest.fixture
async def pair():
    core = FakeCore()
    proxy = create_app(
        core_url="http://core.test",
        tenant_key="tenant-secret-key",
        username="owner",
        password_hash=_hash_password("correct horse battery staple"),
        campaign_id="a63f8494-4730-4cc7-bf2d-0b29e895aa3c",
        static_dir="apps/dashboard/static",
    )
    # route the proxy's upstream through the fake core
    import httpx

    transport = ASGITransport(app=core.app)
    proxy.state.core_client = httpx.AsyncClient(
        transport=transport, base_url="http://core.test"
    )
    transport_p = ASGITransport(app=proxy)
    async with AsyncClient(
        transport=transport_p, base_url="http://dash"
    ) as client:
        yield client, core, proxy
    await proxy.state.core_client.aclose()


AUTH = base64.b64encode(b"owner:correct horse battery staple").decode()
CID = "a63f8494-4730-4cc7-bf2d-0b29e895aa3c"


async def _auth(client):
    return {"Authorization": f"Basic {AUTH}"}


async def test_proxy_requires_basic_auth(pair):
    client, _, _ = pair
    r = await client.get(f"/api/v1/campaigns/{CID}/status")
    assert r.status_code == 401
    assert 'Basic realm="youwei-dashboard"' in r.headers.get(
        "www-authenticate", ""
    )
    # wrong password
    bad = base64.b64encode(b"owner:wrong").decode()
    r2 = await client.get(
        f"/api/v1/campaigns/{CID}/status", headers={"Authorization": f"Basic {bad}"}
    )
    assert r2.status_code == 401


async def test_proxy_forwards_whitelisted_gets_with_tenant_key(pair):
    client, core, _ = pair
    headers = await _auth(client)

    for path in (
        f"/api/v1/campaigns/{CID}/status",
        f"/api/v1/campaigns/{CID}/cases?page_size=50",
        f"/api/v1/campaigns/{CID}/batches/{CID}/reports/20?version=1",
        f"/api/v1/campaigns/{CID}/monthly-reports/2026-10-01",
        "/api/v1/research?limit=20",
        f"/api/v1/research/{CID}",
        f"/api/v1/research/{CID}/report?version=2",
    ):
        r = await client.get(path, headers=headers)
        assert r.status_code == 200, path
        assert r.json()["core"] is True

    forwarded = core.seen
    assert len(forwarded) == 7
    for req in forwarded:
        assert req["method"] == "GET"
        # the tenant key is injected by the proxy, never the client's
        assert req["authorization"] == "Bearer tenant-secret-key"
    assert forwarded[1]["query"] == "page_size=50"
    assert forwarded[2]["query"] == "version=1"


async def test_proxy_blocks_everything_outside_the_whitelist(pair):
    client, core, _ = pair
    headers = await _auth(client)

    # Core write paths must not pass: POST is rejected before any
    # forwarding decision (405 — the proxy route is GET-only)
    assert (
        await client.post("/api/v1/runs", headers=headers, json={})
    ).status_code == 405
    assert (
        await client.get("/api/v1/admin/tenants", headers=headers)
    ).status_code == 404
    # unknown campaign-ish paths
    assert (
        await client.get(f"/api/v1/campaigns/{CID}/secrets", headers=headers)
    ).status_code == 404
    # the tenant key never leaks to a non-whitelisted target
    assert core.seen == []


async def test_proxy_serves_static_and_config(pair):
    client, _, proxy = pair
    headers = await _auth(client)

    r = await client.get("/", headers=headers)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]

    r2 = await client.get("/api/config", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"campaign_id": CID}
    # static files are behind auth too
    unauth = await client.get("/")
    assert unauth.status_code == 401


async def test_proxy_password_hash_is_verified_constant_time():
    import proxy as proxy_mod

    salt = bytes.fromhex("0123456789abcdef")
    digest = hashlib.scrypt(
        "correct horse battery staple".encode(), salt=salt, n=2**14, r=8, p=1
    )
    stored = f"scrypt:{salt.hex()}:{digest.hex()}"
    assert proxy_mod.verify_password("correct horse battery staple", stored)
    assert not proxy_mod.verify_password("wrong", stored)
    assert not proxy_mod.verify_password("", stored)
    # malformed stored hashes fail closed
    assert not proxy_mod.verify_password("x", "not-a-hash")
    assert not proxy_mod.verify_password("x", "scrypt:zz:zz")


async def test_proxy_to_real_core_end_to_end(pg_url, db_engine):
    """The full dashboard chain over the test database: proxy (Basic
    Auth) -> real Core app -> real campaign data. The plan denominator
    and pending-state nulls must survive the whole trip."""
    from youwei_core.api.app import create_app as create_core
    from youwei_core.auth.service import create_api_key, create_tenant
    from youwei_core.config import Settings
    from youwei_core.ledger.scheduler import scheduler_tick
    from test_ledger_campaign import _setup

    tenant = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{tenant}", tenant_id=tenant)
    _, raw_key = await create_api_key(db_engine, tenant, "dash")
    ctx = await _setup(db_engine, tenant, n_panel=1)
    campaign_id = ctx["campaign"].campaign_id
    await scheduler_tick(db_engine)

    core = create_core(Settings(database_url=pg_url, admin_api_key="admin"))
    proxy = create_app(
        core_url="http://core.test",
        tenant_key=raw_key,
        username="owner",
        password_hash=_hash_password("correct horse battery staple"),
        campaign_id=str(campaign_id),
        static_dir=str(
            Path(__file__).resolve().parent.parent / "apps" / "dashboard" / "static"
        ),
    )
    proxy.state.core_client = httpx.AsyncClient(
        transport=ASGITransport(app=core), base_url="http://core.test"
    )
    try:
        transport = ASGITransport(app=proxy)
        async with AsyncClient(transport=transport, base_url="http://dash") as dash:
            auth = {"Authorization": f"Basic {AUTH}"}

            status = (await dash.get(f"/api/v1/campaigns/{campaign_id}/status", headers=auth)).json()
            assert status["plan"]["planned_batches"] == 12
            assert status["plan"]["batches"]["registered"] == 1
            assert status["plan"]["batches"]["pending_registration"] == 11

            cases = (await dash.get(f"/api/v1/campaigns/{campaign_id}/cases", headers=auth)).json()
            assert cases["total"] == 3
            assert all(c["commit"] is None for c in cases["items"])

            report = await dash.get(
                f"/api/v1/campaigns/{campaign_id}/batches/{cases['items'][0]['batch_id']}/reports/20",
                headers=auth,
            )
            assert report.status_code == 404  # nothing scored yet: pending, not fake
    finally:
        await proxy.state.core_client.aclose()
        await core.state.engine.dispose()
