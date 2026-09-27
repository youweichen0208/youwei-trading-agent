"""S05 closeout: ledger archive and late/unconfirmed monitoring.

Acceptance mapped from the plan (S05 last item) and architecture §6:
- an export freezes a campaign's ledger record into self-describing
  files; the manifest chain head is the value to anchor externally
- verify_archive re-verifies WITHOUT the database: file hashes, the
  commit hash chain recomputed from archived rows (a tamper that
  also fixes the file hash still breaks on content recompute),
  outcome revision chains, report content hashes
- verify_against_db checks object references (every cited snapshot
  still present, content intact, raw objects recoverable) and
  reports chain divergence after later commits
- ops monitoring: unconfirmed commits past their deadline are an
  incident on any occurrence; cases whose exit passed long ago with
  no outcome head mean the scheduler is stuck; late confirmations
  are reported informationally (a recorded, legitimate state)
"""

import json
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from youwei_core.config import Settings
from youwei_core.ledger.archive import (
    ArchiveError,
    export_campaign_archive,
    verify_against_db,
    verify_archive,
)
from youwei_core.ledger.evaluation import generate_batch_report
from youwei_core.ledger.monthly import generate_monthly_report
from youwei_core.ledger.outcomes import resolve_outcome
from youwei_core.ledger.service import plan_batch
from youwei_core.data.calendar import next_weekly_cutoff
from youwei_core.ops.service import evaluate_alerts, ops_snapshot
from test_ledger_campaign import _setup
from test_ledger_monthly import _pick_past_month
from test_ledger_outcomes import _ingest_window, _retarget_case, _window_dates
from test_ledger_seal import _seal, _shift_case_window, _sources


async def _archived_scenario(engine, tenant_id, *, n_panel=2):
    """A campaign with sealed commits, resolved outcomes and a D20
    report — the complete ledger record an export freezes."""
    ctx = await _setup(engine, tenant_id, n_panel=n_panel)
    plan = await plan_batch(
        engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    d20 = [plan.case_ids[i * 3 + 1] for i in range(n_panel)]
    entry_date, exit_date, dates = await _window_dates(
        engine, exit_sessions_back=10, span=20
    )
    for cid in d20:
        await _retarget_case(engine, cid, entry_date, exit_date)
        await _shift_case_window(engine, cid, (timedelta(hours=-1), timedelta(hours=1)))
    await _ingest_window(engine, "SPY", ctx["benchmark"], dates, default={"open": 500.0, "close": 500.0})
    for i in range(n_panel):
        await _ingest_window(engine, f"S{i}", ctx["panel"][i], dates, default={"open": 100.0, "close": 105.0})
        await _seal(engine, tenant_id, d20[i], sources=_sources(baseline_p=0.6, quant_p=0.7))
        await resolve_outcome(engine, d20[i])
    await generate_batch_report(engine, plan.batch_id, 20)
    return ctx, plan, d20


# --- export + self-verification ------------------------------------------------


async def test_export_and_self_verify(db_engine, tenant_id, tmp_path):
    ctx, plan, d20 = await _archived_scenario(db_engine, tenant_id)

    # a past-month planned batch + its monthly summary join the ledger
    # record (campaign-policy §4.2: missed weeks stay in the
    # denominator; the summary discloses them as NA)
    month, cutoffs = _pick_past_month()
    await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=cutoffs[0],
        backfilled_plan=True,
    )
    monthly = await generate_monthly_report(db_engine, ctx["campaign"].campaign_id, month)
    assert monthly.created

    manifest = await export_campaign_archive(
        db_engine, ctx["campaign"].campaign_id, tmp_path
    )
    assert manifest["chain"]["head_seq"] == 2  # two sealed commits
    assert manifest["files"]["commits.jsonl"]["rows"] == 2
    assert manifest["files"]["predictions.jsonl"]["rows"] == 6
    assert manifest["files"]["outcome_revisions.jsonl"]["rows"] == 2
    assert manifest["files"]["evaluation_reports.jsonl"]["rows"] == 1
    assert manifest["files"]["monthly_summary_reports.jsonl"]["rows"] == 1
    export_dir = Path(manifest["export_dir"])
    assert (export_dir / "manifest.json").is_file()

    report = verify_archive(export_dir)
    assert report["ok"], report["issues"]
    assert report["chain_head_seq"] == 2
    assert report["chain_head_hash"] == manifest["chain"]["head_hash"]

    # object references intact against the live database
    cross = await verify_against_db(db_engine, export_dir)
    assert cross["ok"], cross["issues"]
    assert cross["snapshots_referenced"] == 4  # 2 outcome snapshots + the quant positions' 2 evidence snapshots
    assert not cross["notes"]

    # a second export writes a fresh directory (append-only by convention)
    manifest2 = await export_campaign_archive(
        db_engine, ctx["campaign"].campaign_id, tmp_path
    )
    assert manifest2["export_dir"] != manifest["export_dir"]


async def test_archive_detects_file_tampering(db_engine, tenant_id, tmp_path):
    ctx, plan, d20 = await _archived_scenario(db_engine, tenant_id)
    manifest = await export_campaign_archive(
        db_engine, ctx["campaign"].campaign_id, tmp_path
    )
    export_dir = Path(manifest["export_dir"])

    # naive tamper: the file hash catches it
    preds = export_dir / "predictions.jsonl"
    preds.write_text(preds.read_text().replace("0.6000000000", "0.9000000000"))
    report = verify_archive(export_dir)
    assert not report["ok"]
    assert any("hash mismatch" in i for i in report["issues"])

    # sophisticated tamper: fix the file hash in the manifest too —
    # the commit content-hash recompute still catches the forgery
    import hashlib

    data = preds.read_bytes()
    manifest_path = export_dir / "manifest.json"
    manifest_json = json.loads(manifest_path.read_text())
    manifest_json["files"]["predictions.jsonl"]["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest_json, sort_keys=True, separators=(",", ":")))
    report = verify_archive(export_dir)
    assert not report["ok"]
    assert any("content hash mismatch" in i for i in report["issues"])


async def test_archive_vs_db_divergence_and_missing_reference(
    db_engine, tenant_id, tmp_path
):
    ctx, plan, d20 = await _archived_scenario(db_engine, tenant_id)
    manifest = await export_campaign_archive(
        db_engine, ctx["campaign"].campaign_id, tmp_path
    )
    export_dir = Path(manifest["export_dir"])

    # a commit sealed AFTER the export: the live chain diverges from
    # the archive (an expected, reported note — the archive itself
    # still self-verifies)
    await _shift_case_window(
        db_engine, plan.case_ids[0], (timedelta(hours=-1), timedelta(hours=1))
    )
    await _seal(db_engine, tenant_id, plan.case_ids[0])

    assert verify_archive(export_dir)["ok"]
    cross = await verify_against_db(db_engine, export_dir)
    assert cross["ok"]
    assert any("diverged" in n for n in cross["notes"])

    # a referenced outcome snapshot cannot even be deleted while the
    # revision cites it — the FK enforces reference integrity; but its
    # CONTENT can be tampered with (snapshots carry no append-only
    # trigger): the object-reference check must catch the corruption
    async with db_engine.begin() as conn:
        snap_id = (
            await conn.execute(
                text("SELECT prices_and_actions_snapshot_id FROM outcome_revisions LIMIT 1")
            )
        ).scalar_one()
    with pytest.raises(Exception, match="foreign key"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM snapshots WHERE id = :id"), {"id": str(snap_id)}
            )
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE snapshots SET content = 'tampered' WHERE id = :id"),
            {"id": str(snap_id)},
        )
    cross = await verify_against_db(db_engine, export_dir)
    assert not cross["ok"]
    assert any("failed verification" in i for i in cross["issues"])


async def test_export_unknown_campaign_rejected(db_engine, tenant_id, tmp_path):
    with pytest.raises(ArchiveError):
        await export_campaign_archive(db_engine, uuid.uuid4(), tmp_path)


# --- ops monitoring ---------------------------------------------------------------


async def test_ops_ledger_health_clean_and_unconfirmed(db_engine, tenant_id):
    ctx, plan, d20 = await _archived_scenario(db_engine, tenant_id)

    snapshot = await ops_snapshot(db_engine)
    assert snapshot["ledger"]["unconfirmed_past_deadline"] == 0
    assert snapshot["ledger"]["unheaded_overdue_outcomes"] == 0
    assert "ledger_unconfirmed_past_deadline" not in evaluate_alerts(
        snapshot, Settings()
    )

    # crash window: a commit sealed but never durably confirmed, and
    # its deadline passes — an incident on any occurrence
    await _shift_case_window(
        db_engine, plan.case_ids[0], (timedelta(hours=-1), timedelta(hours=1))
    )
    await _seal(db_engine, tenant_id, plan.case_ids[0], confirm=False)
    await _shift_case_window(
        db_engine, plan.case_ids[0], (timedelta(hours=-2), timedelta(hours=-1))
    )

    snapshot = await ops_snapshot(db_engine)
    assert snapshot["ledger"]["unconfirmed_past_deadline"] == 1
    assert snapshot["ledger"]["oldest_unconfirmed_age_seconds"] >= 0
    alerts = evaluate_alerts(snapshot, Settings())
    assert "ledger_unconfirmed_past_deadline" in alerts

    # the replay reconciles it — late (the deadline passed), which is
    # a recorded legitimate state, no longer an unconfirmed incident
    await _seal(db_engine, tenant_id, plan.case_ids[0])
    snapshot = await ops_snapshot(db_engine)
    assert snapshot["ledger"]["unconfirmed_past_deadline"] == 0
    assert snapshot["ledger"]["late_confirmations"] == 1
    assert "ledger_unconfirmed_past_deadline" not in evaluate_alerts(
        snapshot, Settings()
    )


async def test_ops_alerts_unheaded_overdue_outcomes(db_engine, tenant_id):
    ctx = await _setup(db_engine, tenant_id, n_panel=1)
    plan = await plan_batch(
        db_engine,
        ctx["campaign"].campaign_id,
        decision_cutoff=next_weekly_cutoff(datetime.now(UTC)),
    )
    # an exit 15 sessions back (~3 weeks) with no outcome head: the
    # resolver's grace policy must have decided it long ago
    entry_date, exit_date, _ = await _window_dates(
        db_engine, exit_sessions_back=15, span=5
    )
    await _retarget_case(db_engine, plan.case_ids[0], entry_date, exit_date)

    snapshot = await ops_snapshot(db_engine)
    assert snapshot["ledger"]["unheaded_overdue_outcomes"] == 1
    assert "ledger_outcomes_not_resolved" in evaluate_alerts(snapshot, Settings())
