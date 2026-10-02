"""S10 slice 1: read-only report APIs (batch + monthly).

The dashboard reads SAVED reports: version, content sha256, code
versions and the frozen content — GET never triggers regeneration,
and an old version keeps the outcome revisions it was computed from
(corrections append a new version; they never rewrite history).

Tenant scoping matches /v1/campaigns/{id}/status: a foreign tenant
gets 404, never 403, so campaign existence does not leak.
"""

import uuid

from sqlalchemy import text

from youwei_core.auth.service import create_api_key, create_tenant
from youwei_core.ledger.monthly import generate_monthly_report
from youwei_core.ledger.scheduler import scheduler_tick
from test_ledger_monthly import _month_fixture
from test_ledger_pipeline import _drive_resolved_d20
from test_ledger_outcomes import _ingest_window


async def _tenant_key(db_engine):
    tenant = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{tenant}", tenant_id=tenant)
    _, raw = await create_api_key(db_engine, tenant, "dash")
    return tenant, {"Authorization": f"Bearer {raw}"}


async def _count_reports(db_engine, batch_id) -> int:
    async with db_engine.begin() as conn:
        return (
            await conn.execute(
                text("SELECT count(*) FROM evaluation_reports WHERE batch_id = :b"),
                {"b": str(batch_id)},
            )
        ).scalar_one()


# --- batch report read ------------------------------------------------------


async def test_batch_report_read_returns_saved_content(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, *_ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)  # generates the D20 report

    r = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}"
        f"/batches/{batch_id}/reports/20",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report_version"] == 1
    assert body["supersedes_report_id"] is None
    assert body["horizon_td"] == 20
    assert body["batch_id"] == str(batch_id)
    assert body["scoring_code_version"]
    assert len(body["content_sha256"]) == 64
    assert body["is_latest_version"] is True
    assert body["created_at"]
    # the saved content itself: coverage + metrics + per-case rows
    # (the report is per-horizon: one panel security -> one D20 case)
    content = body["content"]
    assert content["horizon_td"] == 20
    assert content["coverage"]["planned_cases"] == 1
    assert content["metrics"]["paired_n"] >= 1
    assert len(content["cases"]) == 1
    # every case row pins the outcome revision it was scored against
    for case_row in content["cases"]:
        assert "outcome" in case_row and "revision" in case_row["outcome"]


async def test_batch_report_read_never_triggers_regeneration(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, *_ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)

    url = (
        f"/v1/campaigns/{ctx['campaign'].campaign_id}"
        f"/batches/{batch_id}/reports/20"
    )
    first = (await client.get(url, headers=headers)).json()
    before = await _count_reports(db_engine, batch_id)

    for _ in range(3):
        again = await client.get(url, headers=headers)
        assert again.status_code == 200
        assert again.json() == first  # identical bytes: pure read

    assert await _count_reports(db_engine, batch_id) == before
    tick = await scheduler_tick(db_engine)
    assert not tick["reports_generated"]  # nothing was perturbed


async def test_batch_report_old_version_keeps_frozen_outcome_revision(
    client, db_engine
):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, case_ids, dates, _ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)  # v1

    base = (
        f"/v1/campaigns/{ctx['campaign'].campaign_id}"
        f"/batches/{batch_id}/reports/20"
    )
    v1 = (await client.get(base, headers=headers)).json()
    v1_case = next(c for c in v1["content"]["cases"] if c["case_id"] == str(case_ids[1]))
    assert v1_case["outcome"]["revision"] == 1

    # a vendor correction lands inside the window -> new outcome head
    # revision -> the report regenerates as version 2
    await _ingest_window(
        db_engine,
        "S0",
        ctx["panel"][0],
        dates,
        default={"open": 100.0, "close": 101.0},
    )
    tick = await scheduler_tick(db_engine)
    assert tick["outcomes_corrected"] == 1
    assert any(
        r["batch_id"] == str(batch_id) and r["horizon_td"] == 20
        for r in tick["reports_generated"]
    )

    v2 = (await client.get(base, headers=headers)).json()
    assert v2["report_version"] == 2
    assert v2["is_latest_version"] is True
    v2_case = next(c for c in v2["content"]["cases"] if c["case_id"] == str(case_ids[1]))
    assert v2_case["outcome"]["revision"] == 2

    # the OLD version still answers, byte-identical to what version 1
    # was, still pinned to the outcome revision it was computed from
    old = (await client.get(f"{base}?version=1", headers=headers)).json()
    assert old["report_version"] == 1
    assert old["is_latest_version"] is False
    assert old["content"] == v1["content"]
    old_case = next(c for c in old["content"]["cases"] if c["case_id"] == str(case_ids[1]))
    assert old_case["outcome"]["revision"] == 1


# --- monthly report read ------------------------------------------------------


async def test_monthly_report_read_returns_saved_content(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    f = await _month_fixture(db_engine, tenant)
    campaign_id = f["ctx"]["campaign"].campaign_id
    result = await generate_monthly_report(db_engine, campaign_id, f["month"])
    assert result.created

    r = await client.get(
        f"/v1/campaigns/{campaign_id}/monthly-reports/{f['month'].isoformat()}",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report_version"] == 1
    assert body["month"] == f"{f['month'].isoformat()}"
    assert body["monthly_code_version"]
    assert body["scoring_code_version"]
    assert len(body["content_sha256"]) == 64
    assert body["is_latest_version"] is True
    # monthly content aggregates per-batch point estimates and keeps
    # the batch-report references (id/version/sha) it aggregated
    content = body["content"]
    assert content["month"] == f["month"].isoformat()
    entries = content["batches"]
    assert {e["batch_id"] for e in entries} >= {str(b) for b in f["batch_ids"]}
    for entry in entries:
        if entry["d20_report"] is not None:
            ref = entry["d20_report"]
            assert ref["report_id"] and ref["report_version"] >= 1
            assert len(ref["content_sha256"]) == 64


# --- scoping and not-found semantics -----------------------------------------


async def test_report_api_tenant_scoping(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, *_ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)

    url = (
        f"/v1/campaigns/{ctx['campaign'].campaign_id}"
        f"/batches/{batch_id}/reports/20"
    )
    # unauthenticated
    assert (await client.get(url)).status_code == 401

    # a foreign tenant must not learn the campaign exists
    other, other_headers = await _tenant_key(db_engine)
    r = await client.get(url, headers=other_headers)
    assert r.status_code == 404
    assert other != tenant


async def test_report_api_not_found_paths(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, *_ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)

    base = f"/v1/campaigns/{ctx['campaign'].campaign_id}"
    # horizon outside the registered target specs: not a valid read
    assert (
        await client.get(f"{base}/batches/{batch_id}/reports/7", headers=headers)
    ).status_code == 422
    # no report for an ungenerated horizon yet
    assert (
        await client.get(f"{base}/batches/{batch_id}/reports/1", headers=headers)
    ).status_code == 404
    # unknown version
    assert (
        await client.get(f"{base}/batches/{batch_id}/reports/20?version=99", headers=headers)
    ).status_code == 404
    # batch from a different campaign
    stranger = uuid.uuid4()
    assert (
        await client.get(f"{base}/batches/{stranger}/reports/20", headers=headers)
    ).status_code == 404
    # month with no report
    assert (
        await client.get(f"{base}/monthly-reports/2020-01-01", headers=headers)
    ).status_code == 404
