"""S10 slice 2: frozen-plan denominator + case listing API.

The campaign view must show the FROZEN plan as the denominator —
planned batches x panel size x horizons — and classify each planned
cutoff: future cutoffs that are not yet pre-registered are PENDING,
not failures; a past cutoff with no batch means the scheduler is
behind; a batch registered late stays as an explicit missed record.
Per batch, a phase says awaiting_cutoff / in_window / sealed / missed.

The case list is read-only and paginated, shows per-source positions
(in Phase 1A llm_adjusted is sealed as unavailable/not_enabled and
must surface as such, not vanish), and separates the CURRENT outcome
head from the revision the latest report scored against. Cases
without a commit or outcome are normal states, rendered as nulls —
never failures, never zeros.
"""

import uuid

from youwei_core.auth.service import create_api_key, create_tenant
from youwei_core.ledger.scheduler import scheduler_tick
from test_ledger_campaign import _setup
from test_ledger_monthly import _month_fixture
from test_ledger_pipeline import _drive_resolved_d20


async def _tenant_key(db_engine):
    tenant = uuid.uuid4()
    await create_tenant(db_engine, f"tenant-{tenant}", tenant_id=tenant)
    _, raw = await create_api_key(db_engine, tenant, "dash")
    return tenant, {"Authorization": f"Bearer {raw}"}


# --- frozen plan denominator -------------------------------------------------


async def test_status_shows_frozen_plan_denominator(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx = await _setup(db_engine, tenant, n_panel=2)  # 12 planned cutoffs
    await scheduler_tick(db_engine)  # pre-registers the coming batch

    r = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}/status", headers=headers
    )
    assert r.status_code == 200
    plan = r.json()["plan"]
    assert plan["planned_batches"] == 12
    assert plan["panel_size"] == 2
    assert plan["horizons"] == [1, 20, 60]
    assert plan["planned_cases"] == 12 * 2 * 3
    assert plan["batches"]["registered"] == 1
    assert plan["batches"]["pending_registration"] == 11
    assert plan["batches"]["overdue_unregistered"] == 0
    assert plan["batches"]["backfilled"] == 0
    # the registered batch is still waiting for its cutoff
    assert r.json()["batches"][0]["phase"] == "awaiting_cutoff"


async def test_status_flags_backfilled_and_missed_batches(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    f = await _month_fixture(db_engine, tenant)
    campaign_id = f["ctx"]["campaign"].campaign_id

    r = await client.get(
        f"/v1/campaigns/{campaign_id}/status", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    plan = body["plan"]
    assert plan["planned_batches"] == 5  # the month holds five Saturdays

    # the never-run week is an explicit missed record, not a hole;
    # the month fixture registers 3 of the month's 5 planned cutoffs
    # (all backfilled) — the remaining past cutoffs count as overdue
    # unregistered (the scheduler would backfill them on its next tick)
    by_id = {b["batch_id"]: b for b in body["batches"]}
    missed = by_id[str(f["missed_batch_id"])]
    assert missed["phase"] == "missed"
    assert missed["planned_cases"] > 0 and missed["with_commit"] == 0
    for batch_id in f["batch_ids"]:
        assert by_id[str(batch_id)]["phase"] == "sealed"
    assert plan["batches"]["registered"] == 3
    assert plan["batches"]["backfilled"] == 3
    assert plan["batches"]["pending_registration"] == 0
    assert plan["batches"]["overdue_unregistered"] == (
        plan["planned_batches"] - 3
    )


# --- case listing ------------------------------------------------------------


async def test_cases_api_rows_sources_and_outcome(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, case_ids, *_ = await _drive_resolved_d20(db_engine, tenant)
    await scheduler_tick(db_engine)  # D20 report exists

    r = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}/cases?horizon_td=20",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    # the fixture ends up with two D20 cases: the driven one (sealed,
    # resolved, scored) and one from the re-registered original cutoff
    # (still pending — a normal state, not a failure)
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["enabled_sources"] == ["baseline", "quant_model"]

    row = next(i for i in body["items"] if i["case_id"] == str(case_ids[1]))
    pending = next(i for i in body["items"] if i["case_id"] != str(case_ids[1]))
    assert pending["commit"] is None and pending["outcome_head"] is None
    assert row["batch_id"] == str(batch_id)
    assert row["horizon_td"] == 20
    assert row["security_symbol"] == "S0"
    assert row["benchmark_security_id"]

    # sealed commit with all three source positions; llm_adjusted is
    # Phase-1A not_enabled and must surface as unavailable, not vanish
    assert row["commit"] is not None
    assert row["commit"]["timeliness"] in ("on_time", "late")
    sources = row["commit"]["sources"]
    assert set(sources) == {"baseline", "quant_model", "llm_adjusted"}
    assert sources["baseline"]["source_status"] in ("produced", "fallback")
    assert sources["quant_model"]["source_status"] in ("produced", "fallback")
    assert sources["llm_adjusted"]["source_status"] == "unavailable"
    assert sources["llm_adjusted"]["reason"] == "not_enabled"

    # current outcome head vs the revision the latest report scored
    assert row["outcome_head"]["status"] == "resolved"
    assert row["outcome_head"]["revision"] == 1
    assert row["scored_against_revision"] == 1
    assert row["label_mature"] is True


async def test_cases_api_pending_states_are_normal_not_failures(client, db_engine):
    """A freshly pre-registered batch has no commit, no outcome, no
    report: every one of those is a null, not an error or a zero."""
    tenant, headers = await _tenant_key(db_engine)
    ctx = await _setup(db_engine, tenant, n_panel=1)
    await scheduler_tick(db_engine)

    r = await client.get(
        f"/v1/campaigns/{ctx['campaign'].campaign_id}/cases", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    for row in body["items"]:
        assert row["commit"] is None
        assert row["outcome_head"] is None
        assert row["scored_against_revision"] is None
        assert row["label_mature"] is False
        assert row["horizon_td"] in (1, 20, 60)


async def test_cases_api_pagination_and_batch_filter(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx, batch_id, *_ = await _drive_resolved_d20(db_engine, tenant)

    base = f"/v1/campaigns/{ctx['campaign'].campaign_id}/cases"
    all_rows = (await client.get(base, headers=headers)).json()
    assert all_rows["total"] == 6  # two batches x one security x three horizons

    page1 = (await client.get(f"{base}?page_size=2", headers=headers)).json()
    assert len(page1["items"]) == 2
    assert page1["total"] == 6
    assert page1["pages"] == 3

    page3 = (await client.get(f"{base}?page_size=2&page=3", headers=headers)).json()
    assert len(page3["items"]) == 2
    seen = {i["case_id"] for i in page1["items"] + page3["items"]}
    seen |= {i["case_id"] for i in (await client.get(f"{base}?page_size=2&page=2", headers=headers)).json()["items"]}
    assert seen == {i["case_id"] for i in all_rows["items"]}

    filtered = (
        await client.get(f"{base}?batch_id={batch_id}&horizon_td=1", headers=headers)
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["horizon_td"] == 1


# --- scoping -------------------------------------------------------------------


async def test_cases_api_tenant_scoping(client, db_engine):
    tenant, headers = await _tenant_key(db_engine)
    ctx = await _setup(db_engine, tenant, n_panel=1)
    await scheduler_tick(db_engine)

    url = f"/v1/campaigns/{ctx['campaign'].campaign_id}/cases"
    assert (await client.get(url)).status_code == 401
    _, other_headers = await _tenant_key(db_engine)
    assert (await client.get(url, headers=other_headers)).status_code == 404

