"""S02a acceptance scenario: duplicate delivery.

A client submits a run; the response is lost; the client retries with
the same Idempotency-Key and the same payload -> the same run comes
back (200, not a second run). The same key with a different payload is
rejected (409). Keys are scoped per tenant.
"""

BODY = {
    "kind": "research",
    "total_budget_micros": 1_000_000,
    "jobs": [{"kind": "noop", "payload": {"x": 1}}],
}


def _post(client, body, key, headers):
    return client.post(
        "/v1/runs", json=body, headers={**headers, "Idempotency-Key": key}
    )


async def test_retry_after_lost_response_returns_same_run(client, tenant_headers):
    r1 = await _post(client, BODY, "k1", tenant_headers)
    assert r1.status_code == 201
    r2 = await _post(client, BODY, "k1", tenant_headers)
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert r2.json()["status"] == r1.json()["status"]


async def test_same_key_different_payload_conflicts(client, tenant_headers):
    r1 = await _post(client, BODY, "k2", tenant_headers)
    assert r1.status_code == 201
    other = {**BODY, "jobs": [{"kind": "noop", "payload": {"x": 2}}]}
    r2 = await _post(client, other, "k2", tenant_headers)
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "idempotency_payload_mismatch"


async def test_keys_are_tenant_scoped(client, tenant_headers, other_tenant_headers):
    r1 = await _post(client, BODY, "shared", tenant_headers)
    r2 = await _post(client, BODY, "shared", other_tenant_headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


async def test_run_view_lists_jobs_and_budget(client, tenant_headers):
    r = await _post(client, BODY, "k3", tenant_headers)
    assert r.status_code == 201
    view = r.json()
    assert view["status"] == "pending"
    assert view["total_budget_micros"] == 1_000_000
    assert view["reserved_micros"] == 0
    assert view["settled_micros"] == 0
    assert len(view["jobs"]) == 1
    assert view["jobs"][0]["status"] == "queued"
    assert view["jobs"][0]["max_attempts"] == 1


async def test_submission_event_recorded(client, tenant_headers):
    r = await _post(client, BODY, "k4", tenant_headers)
    run_id = r.json()["id"]
    ev = await client.get(f"/v1/runs/{run_id}/events", headers=tenant_headers)
    assert ev.status_code == 200
    events = ev.json()["events"]
    assert [e["event_type"] for e in events] == ["run.submitted"]
    assert events[0]["payload"]["job_count"] == 1
    assert events[0]["published_at"] is None  # outbox: unpublished until publisher runs


async def test_missing_headers_rejected(client):
    r = await client.post("/v1/runs", json=BODY)
    assert r.status_code == 422
