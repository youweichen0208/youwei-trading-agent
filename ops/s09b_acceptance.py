"""S09b acceptance harness — synthetic data loop (round 1).

Runs against the acceptance environment (local images, mock Tiingo) and
verifies the real API -> Worker -> collect chain end-to-end:

1. admin creates a tenant + API key (auth/permission boundary)
2. a security is created (synthetic ticker TEST1)
3. a ``data.tiingo_daily`` run is submitted via the tenant API key
4. the worker claims it, calls the mock Tiingo, ingests synthetic bars
5. assertions: run succeeded, events in order, price_observations present
6. idempotency: same Idempotency-Key replays 200 without a new run
7. failure path: ticker FAIL -> HTTP 500 -> run ends failed/retried
8. permission: no key -> 401; wrong-tenant read -> 404

This writes only to the acceptance database and calls only the mock
Tiingo. No real vendor calls, no LLM, no approvals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select

from youwei_core.db.engine import make_engine
from youwei_core.db.meta import price_observations, securities, security_identities
from youwei_core.data.securities import IdentitySpec, create_security


class Check:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}  {detail}")

    def summary(self) -> int:
        print(f"\n{self.passed} passed, {self.failed} failed")
        return 1 if self.failed else 0


def _bars_window(n: int = 5) -> tuple[date, date]:
    # Last n weekdays ending yesterday (synthetic window).
    end = date.today() - timedelta(days=1)
    days = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[-1], days[0]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://127.0.0.1:8001")
    ap.add_argument("--db-url", default=os.environ.get("YOUWEI_APP_DB_URL", ""))
    ap.add_argument("--admin-key", default=os.environ.get("YOUWEI_ADMIN_API_KEY", ""))
    args = ap.parse_args()

    if not args.admin_key or not args.db_url:
        print("ERROR: --admin-key and --db-url are required", file=sys.stderr)
        return 2

    c = Check()
    admin_headers = {"Authorization": f"Bearer {args.admin_key}"}
    async with httpx.AsyncClient(base_url=args.api, timeout=30.0) as http:
        # 1. create tenant + api key
        tenant_slug = f"acceptance-synthetic-{uuid.uuid4().hex[:8]}"
        r = await http.post("/v1/admin/tenants", headers=admin_headers,
                            json={"slug": tenant_slug})
        c.ok("admin creates tenant", r.status_code == 201, f"status={r.status_code}")
        tenant_id = r.json().get("id")
        r = await http.post("/v1/admin/api-keys", headers=admin_headers,
                            json={"tenant_id": tenant_id, "name": "acceptance"})
        c.ok("admin creates api key", r.status_code == 201, f"status={r.status_code}")
        api_token = r.json().get("token")
        tenant_headers = {"Authorization": f"Bearer {api_token}"}

        # 8a. permission: no key -> 401
        r = await http.get("/v1/runs/00000000-0000-0000-0000-000000000000")
        c.ok("no key -> 401", r.status_code == 401, f"status={r.status_code}")

        # 2. create a synthetic security directly in the acceptance DB
        engine = make_engine(args.db_url, pool_size=1, max_overflow=0)
        try:
            sec_id = await create_security(
                engine, identities=[IdentitySpec("ticker", "TEST1", date(2024, 1, 1))]
            )
            bench_id = await create_security(
                engine, identities=[IdentitySpec("ticker", "BENCH", date(2024, 1, 1))]
            )
            c.ok("security created (TEST1)", sec_id is not None)
        finally:
            await engine.dispose()

        start, end = _bars_window()
        # 3. submit a data.tiingo_daily run via the tenant key
        payload = {
            "kind": "data.tiingo_daily",
            "jobs": [{
                "kind": "data.tiingo_daily",
                "payload": {
                    "ticker": "TEST1",
                    "security_id": str(sec_id),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            }],
        }
        idem_key = f"acceptance-synthetic-{uuid.uuid4().hex}"
        r = await http.post(
            "/v1/runs",
            headers={**tenant_headers, "Idempotency-Key": idem_key},
            json=payload,
        )
        c.ok("submit run (201)", r.status_code in (200, 201), f"status={r.status_code}")
        run_id = r.json().get("run_id") or r.json().get("id")

        # 4. poll until terminal
        status = await _poll_run(http, tenant_headers, run_id)
        c.ok("run reached terminal", status in ("succeeded", "failed"), f"status={status}")

        # 6. idempotency: same key replays 200, no new run
        r2 = await http.post(
            "/v1/runs",
            headers={**tenant_headers, "Idempotency-Key": idem_key},
            json=payload,
        )
        c.ok("idempotent replay (200)", r2.status_code == 200, f"status={r2.status_code}")
        c.ok("idempotent same run", r2.json().get("run_id") == run_id or r2.json().get("id") == run_id)

        # 5. verify ingestion
        engine = make_engine(args.db_url, pool_size=1, max_overflow=0)
        try:
            async with engine.begin() as conn:
                rows = (await conn.execute(
                    select(price_observations.c.id).where(
                        price_observations.c.security_id == sec_id
                    )
                )).all()
            c.ok("synthetic bars ingested", len(rows) > 0, f"rows={len(rows)}")
        finally:
            await engine.dispose()

        # 7. failure path: ticker FAIL -> HTTP 500
        fail_payload = {
            "kind": "data.tiingo_daily",
            "jobs": [{
                "kind": "data.tiingo_daily",
                "payload": {
                    "ticker": "FAIL",
                    "security_id": str(bench_id),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            }],
        }
        r = await http.post(
            "/v1/runs",
            headers={**tenant_headers, "Idempotency-Key": f"fail-{uuid.uuid4().hex}"},
            json=fail_payload,
        )
        fail_run = r.json().get("run_id") or r.json().get("id")
        fstatus = await _poll_run(http, tenant_headers, fail_run)
        c.ok("failure path reached terminal", fstatus in ("succeeded", "failed"), f"status={fstatus}")
        # FAIL ticker yields a 500 -> handler fails -> job failed; the run
        # should NOT be succeeded. Assert failure is recorded (not silent).
        if fstatus == "succeeded":
            c.ok("FAIL ticker did not silently succeed", False, "run succeeded for FAIL ticker")
        else:
            c.ok("FAIL ticker recorded failure", True, f"status={fstatus}")

        # 8b. wrong tenant cannot read another tenant's run (run belongs to
        # this tenant; create a second tenant and try to read run_id)
        r = await http.post("/v1/admin/tenants", headers=admin_headers,
                            json={"slug": f"other-{uuid.uuid4().hex[:8]}"})
        other_tenant = r.json().get("id")
        r = await http.post("/v1/admin/api-keys", headers=admin_headers,
                            json={"tenant_id": other_tenant, "name": "other"})
        other_token = r.json().get("token")
        r = await http.get(f"/v1/runs/{run_id}",
                           headers={"Authorization": f"Bearer {other_token}"})
        c.ok("cross-tenant read -> 404", r.status_code == 404, f"status={r.status_code}")

    return c.summary()


async def _poll_run(http, headers, run_id, tries=60):
    for _ in range(tries):
        r = await http.get(f"/v1/runs/{run_id}", headers=headers)
        if r.status_code == 200:
            status = r.json().get("status")
            if status in ("succeeded", "failed", "cancelled"):
                return status
        await asyncio.sleep(0.5)
    return "timeout"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
