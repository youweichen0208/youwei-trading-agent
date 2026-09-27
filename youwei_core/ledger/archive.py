"""Ledger archive (S05 closeout, architecture §6).

The OSS archive bucket was excluded from the MVP scope; the archive
contract is therefore local files with the same guarantees the
architecture demands:

- an export is a frozen, self-describing directory: JSONL rows for
  the campaign's ledger records plus a manifest with per-file content
  hashes, the chain head at export time and the release references
- the manifest's chain head hash is the value to anchor EXTERNALLY
  (recorded by the human owner outside this system, e.g. in Git or a
  note); local history can be rewritten, the anchored hash cannot
- verify_archive() re-verifies the export WITHOUT the database: file
  hashes, the full commit hash chain (content hashes recomputed from
  the archived rows), outcome revision chains and report content
  hashes — the archive is the tamper-evident evidence
- verify_against_db() checks object references: every snapshot the
  ledger rows point to must still exist with its recorded content
  hash, and its raw objects must be intact (对象引用恢复) — plus it
  reports divergence between the archived head and the live chain
  (expected when new commits sealed after the export)

Exports are append-only by convention: never edit or delete archive
directories; a new export always writes a fresh timestamped directory
(the tamper-evidence point of the archive).
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import (
    campaigns,
    evaluation_reports,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    outcome_revisions,
    predictions,
    research_releases,
)
from youwei_core.ledger.sealing import GENESIS_HASH, _content_hash
from youwei_core.ledger.service import canonical_json, decimal_str

ARCHIVE_FORMAT_VERSION = "ledger-archive-v1"


class ArchiveError(Exception):
    pass


def _convert(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_str(value)
    return value


def _serialize(row) -> str:
    return canonical_json({k: _convert(v) for k, v in row.items()})


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_jsonl(path: Path, rows) -> tuple[int, str]:
    lines = [_serialize(row) + "\n" for row in rows]
    data = "".join(lines).encode("utf-8")
    path.write_bytes(data)
    return len(lines), _sha256_bytes(data)


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# --- export --------------------------------------------------------------------


async def export_campaign_archive(
    engine: AsyncEngine, campaign_id: uuid.UUID, target_dir: Path
) -> dict:
    """Write the campaign's complete ledger record to a fresh
    timestamped directory under target_dir. Returns the manifest."""
    target = Path(target_dir)
    export_dir = target / f"campaign-{campaign_id}" / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    export_dir.mkdir(parents=True, exist_ok=False)

    async with engine.begin() as conn:
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == campaign_id)
            )
        ).mappings().one_or_none()
        if campaign is None:
            raise ArchiveError(f"campaign {campaign_id} not found")
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.id == campaign.release_row_id
                )
            )
        ).mappings().one()
        case_ids = (
            await conn.execute(
                select(forecast_cases.c.id).where(
                    forecast_cases.c.campaign_id == campaign_id
                )
            )
        ).scalars().all()

        commit_rows, prediction_rows, event_rows = [], [], []
        if case_ids:
            commit_rows = (
                (
                    await conn.execute(
                        select(forecast_commits)
                        .where(forecast_commits.c.case_id.in_(case_ids))
                        .order_by(forecast_commits.c.chain_id, forecast_commits.c.chain_seq)
                    )
                )
                .mappings()
                .all()
            )
            if commit_rows:
                prediction_rows = (
                    (
                        await conn.execute(
                            select(predictions)
                            .where(predictions.c.commit_id.in_([c.id for c in commit_rows]))
                            .order_by(predictions.c.commit_id, predictions.c.source)
                        )
                    )
                    .mappings()
                    .all()
                )
                event_rows = (
                    (
                        await conn.execute(
                            select(forecast_commit_events)
                            .where(
                                forecast_commit_events.c.commit_id.in_(
                                    [c.id for c in commit_rows]
                                )
                            )
                            .order_by(
                                forecast_commit_events.c.commit_id,
                                forecast_commit_events.c.occurred_at,
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
        outcome_rows = (
            (
                await conn.execute(
                    select(outcome_revisions)
                    .where(outcome_revisions.c.case_id.in_(case_ids))
                    .order_by(outcome_revisions.c.case_id, outcome_revisions.c.revision)
                )
            )
            .mappings()
            .all()
            if case_ids
            else []
        )
        report_rows = (
            (
                await conn.execute(
                    select(evaluation_reports)
                    .where(evaluation_reports.c.campaign_id == campaign_id)
                    .order_by(
                        evaluation_reports.c.batch_id,
                        evaluation_reports.c.horizon_td,
                        evaluation_reports.c.report_version,
                    )
                )
            )
            .mappings()
            .all()
        )

    files = {}
    for name, rows in (
        ("commits.jsonl", commit_rows),
        ("predictions.jsonl", prediction_rows),
        ("commit_events.jsonl", event_rows),
        ("outcome_revisions.jsonl", outcome_rows),
        ("evaluation_reports.jsonl", report_rows),
    ):
        count, sha = _write_jsonl(export_dir / name, rows)
        files[name] = {"rows": count, "sha256": sha}

    head_row = commit_rows[-1] if commit_rows else None
    manifest = {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "campaign_id": str(campaign_id),
        "campaign_key": campaign.campaign_key,
        "release_id": release.release_id,
        "release_content_sha256": release.release_content_sha256,
        "chain": {
            "chain_id": f"campaign:{campaign_id}",
            "head_seq": 0 if head_row is None else head_row.chain_seq,
            "head_hash": GENESIS_HASH if head_row is None else head_row.content_sha256,
        },
        "files": files,
        "exported_at": datetime.now(UTC).isoformat(),
    }
    (export_dir / "manifest.json").write_text(
        canonical_json(manifest), encoding="utf-8"
    )
    manifest["export_dir"] = str(export_dir)
    return manifest


# --- verification -----------------------------------------------------------------


def verify_archive(export_dir: Path) -> dict:
    """Self-verify an export without the database: file hashes, the
    commit hash chain recomputed from archived rows, outcome revision
    chains, report content hashes and the manifest head."""
    export_dir = Path(export_dir)
    manifest_path = export_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ArchiveError(f"no manifest.json under {export_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
        raise ArchiveError(
            f"unknown archive format {manifest.get('format_version')!r}"
        )

    issues: list[str] = []

    # file hashes
    data: dict[str, list[dict]] = {}
    for name, meta in manifest["files"].items():
        path = export_dir / name
        if not path.is_file():
            issues.append(f"archive file missing: {name}")
            continue
        actual = _sha256_bytes(path.read_bytes())
        if actual != meta["sha256"]:
            issues.append(f"archive file hash mismatch: {name}")
        rows = _read_jsonl(path)
        if len(rows) != meta["rows"]:
            issues.append(f"archive row count mismatch: {name}")
        data[name] = rows

    # commit chain recomputed from the archive itself
    commits = data.get("commits.jsonl", [])
    preds_by_commit: dict[str, list[dict]] = {}
    for p in data.get("predictions.jsonl", []):
        preds_by_commit.setdefault(p["commit_id"], []).append(p)
    prev_hash = None
    for i, commit in enumerate(commits):
        if commit["chain_seq"] != i + 1:
            issues.append(
                f"archive commit seq gap at position {i}: {commit['chain_seq']}"
            )
        expected_prev = GENESIS_HASH if i == 0 else prev_hash
        if commit["prev_hash"] != expected_prev:
            issues.append(f"archive commit {commit['chain_seq']} breaks the chain")
        entries = sorted(preds_by_commit.get(commit["id"], []), key=lambda p: p["source"])
        recomputed = _content_hash(
            case_id=commit["case_id"],
            release_id=manifest["release_id"],
            chain_id=commit["chain_id"],
            chain_seq=commit["chain_seq"],
            prev_hash=commit["prev_hash"],
            sealed_at=datetime.fromisoformat(commit["sealed_at"]),
            attempt_id=commit["attempt_id"],
            attempt_no=commit["attempt_no"],
            input_manifest=commit["input_manifest"],
            entries=[
                {
                    "source": p["source"],
                    "source_status": p["source_status"],
                    "reason": p["reason"],
                    "p_outperform": p["p_outperform"],
                    "expected_excess_return": p["expected_excess_return"],
                    "evidence_snapshot_id": p["evidence_snapshot_id"],
                    "model_version": p["model_version"],
                }
                for p in entries
            ],
        )
        if recomputed != commit["content_sha256"]:
            issues.append(
                f"archive commit {commit['chain_seq']} content hash mismatch"
            )
        prev_hash = commit["content_sha256"]

    if commits:
        if manifest["chain"]["head_seq"] != commits[-1]["chain_seq"]:
            issues.append("manifest head_seq does not match the archived chain")
        if manifest["chain"]["head_hash"] != commits[-1]["content_sha256"]:
            issues.append("manifest head_hash does not match the archived chain")
    elif manifest["chain"]["head_seq"] != 0:
        issues.append("manifest claims commits but none are archived")

    # outcome revision chains per case
    by_case: dict[str, list[dict]] = {}
    for rev in data.get("outcome_revisions.jsonl", []):
        by_case.setdefault(rev["case_id"], []).append(rev)
    for case_id, revs in by_case.items():
        revs.sort(key=lambda r: r["revision"])
        prev_id = None
        for i, rev in enumerate(revs):
            if rev["revision"] != i + 1:
                issues.append(f"archive outcome numbering breaks for case {case_id}")
            expected_parent = None if i == 0 else prev_id
            if rev["supersedes_outcome_id"] != expected_parent:
                issues.append(
                    f"archive outcome chain breaks for case {case_id} at revision {rev['revision']}"
                )
            prev_id = rev["id"]

    # report content hashes
    for report in data.get("evaluation_reports.jsonl", []):
        actual = hashlib.sha256(
            canonical_json(report["content"]).encode("utf-8")
        ).hexdigest()
        if actual != report["content_sha256"]:
            issues.append(
                f"archive report content hash mismatch: {report['id']}"
            )

    return {
        "ok": not issues,
        "issues": issues,
        "chain_head_seq": manifest["chain"]["head_seq"],
        "chain_head_hash": manifest["chain"]["head_hash"],
        "rows": {k: len(v) for k, v in data.items()},
    }


async def verify_against_db(engine: AsyncEngine, export_dir: Path) -> dict:
    """Cross-check an export against the live database: the chain head
    (divergence is expected when new commits sealed after the export),
    and every referenced snapshot still present with its recorded
    content hash and intact raw objects (对象引用恢复)."""
    from youwei_core.data.snapshots import verify_snapshot
    from youwei_core.db.meta import ledger_chains

    export_dir = Path(export_dir)
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    issues: list[str] = []
    notes: list[str] = []

    async with engine.begin() as conn:
        chain = (
            await conn.execute(
                select(ledger_chains).where(
                    ledger_chains.c.chain_id == manifest["chain"]["chain_id"]
                )
            )
        ).mappings().one_or_none()
    if chain is None:
        issues.append("live chain row missing for the archived campaign")
    elif (chain.head_seq, chain.head_hash) != (
        manifest["chain"]["head_seq"],
        manifest["chain"]["head_hash"],
    ):
        notes.append(
            f"live chain diverged from the archive: db head {chain.head_seq}/"
            f"{chain.head_hash[:12]} vs archived {manifest['chain']['head_seq']}/"
            f"{manifest['chain']['head_hash'][:12]} (new commits after export?)"
        )

    # object references: snapshots cited by ledger rows
    referenced: set[str] = set()
    for p in _read_jsonl(export_dir / "predictions.jsonl"):
        if p["evidence_snapshot_id"]:
            referenced.add(p["evidence_snapshot_id"])
    for rev in _read_jsonl(export_dir / "outcome_revisions.jsonl"):
        if rev["prices_and_actions_snapshot_id"]:
            referenced.add(rev["prices_and_actions_snapshot_id"])

    from youwei_core.data.snapshots import SnapshotNotFound, SnapshotCorrupt

    for snap_id in sorted(referenced):
        try:
            checks = await verify_snapshot(engine, uuid.UUID(snap_id))
        except SnapshotNotFound:
            issues.append(f"referenced snapshot {snap_id} is gone from the database")
            continue
        except SnapshotCorrupt:
            issues.append(f"referenced snapshot {snap_id} failed its content hash")
            continue
        if not checks.get("ok"):
            issues.append(
                f"referenced snapshot {snap_id} failed verification: {checks}"
            )

    return {
        "ok": not issues,
        "issues": issues,
        "notes": notes,
        "snapshots_referenced": len(referenced),
    }
