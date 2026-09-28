"""Campaign registration and batch planning (S05, campaign-policy v1).

Registration layer:
- research_releases: immutable manifests + human approval records; a
  campaign may only reference a release whose content hash carries at
  least one approval. The approving principal is a human; this module
  only records the act, it cannot perform it.
- campaigns: pre-registered multi-week plans fixing panel, target
  specs, enabled sources, time protocol and release. Phase 1A policy
  is enforced here: enabled sources are exactly baseline + quant_model
  with no fallback.
- forecast_batches / forecast_cases: per-cutoff weekly instances with
  sealed instance windows resolved from the versioned calendar. A
  cutoff already in the past can only be planned as an explicit
  backfilled record of a missed week.

Every public operation is one short transaction.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.calendar import (
    BatchTimes,
    resolve_batch_times,
    session_times,
    trading_day_offset,
)
from youwei_core.db.meta import (
    HORIZONS_TD,
    campaigns,
    events,
    forecast_batches,
    forecast_cases,
    ledger_chains,
    release_approvals,
    research_releases,
    securities,
    training_manifests,
)

# campaign-policy §3 (Phase 1A)
PHASE1A_ENABLED_SOURCES = ("baseline", "quant_model")
PHASE1A_FALLBACK_POLICY = "phase1a-none"

# campaign-policy §3 (Phase 1B): llm_adjusted enabled with a quant fallback
# for LLM failure/timeout. Enabling still requires a new Campaign + a
# human-approved release; these constants register the policy the sealing
# path already knows how to enforce.
PHASE1B_ENABLED_SOURCES = ("baseline", "quant_model", "llm_adjusted")
PHASE1B_FALLBACK_POLICY = "phase1b-llm-from-quant"

GENESIS_HASH = hashlib.sha256(b"youwei-ledger-genesis-v1").hexdigest()


class ReleaseConflict(Exception):
    """Same release_id with different content."""


class ReleaseNotFound(Exception):
    pass


class ApprovalMismatch(Exception):
    """Approval references a stale release content hash."""


class CampaignValidationError(Exception):
    pass


class CampaignConflict(Exception):
    """Same (tenant, campaign_key) with different content."""


class CampaignNotFound(Exception):
    pass


class PlanError(Exception):
    pass


class PlanBackfillRequired(PlanError):
    """The cutoff is already in the past: planning it is a backfilled
    record of a missed week and must say so explicitly."""


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def decimal_str(value) -> str:
    """Canonical fixed-point string for a quantized Decimal.
    str(Decimal) is not stable for zero ('0E-10' vs '0.0000000000'),
    which would break hash recomputation; format() always renders the
    full scale."""
    return format(value, "f")


# --- releases ---------------------------------------------------------------


@dataclass
class ReleaseRecord:
    release_row_id: uuid.UUID
    release_id: str
    release_content_sha256: str
    created: bool


def release_content_hash(manifest: dict) -> str:
    """campaign-policy §5: hash over the canonical manifest, excluding
    the hash itself and any approval records (approvals live in their
    own table and never enter this computation)."""
    content = {k: v for k, v in manifest.items() if k != "release_content_hash"}
    return sha256_hex(content)


async def register_release(
    engine: AsyncEngine, *, release_id: str, manifest: dict
) -> ReleaseRecord:
    """Idempotent release registration. Same id + same manifest ->
    existing; same id + different manifest -> ReleaseConflict."""
    content_sha = release_content_hash(manifest)
    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).mappings().first()
        if existing is not None:
            if existing.release_content_sha256 != content_sha:
                raise ReleaseConflict(
                    f"release {release_id!r} already exists with different content"
                )
            return ReleaseRecord(
                release_row_id=existing.id,
                release_id=release_id,
                release_content_sha256=content_sha,
                created=False,
            )
        row_id = uuid.uuid4()
        await conn.execute(
            research_releases.insert().values(
                id=row_id,
                release_id=release_id,
                manifest=manifest,
                release_content_sha256=content_sha,
            )
        )
    return ReleaseRecord(
        release_row_id=row_id,
        release_id=release_id,
        release_content_sha256=content_sha,
        created=True,
    )


async def approve_release(
    engine: AsyncEngine,
    *,
    release_id: str,
    approver_principal_id: str,
    scope: str,
    basis: str | None = None,
) -> None:
    """Record a human approval of the release's current content hash.
    Idempotent per (release, approver); an approval whose stored hash
    no longer matches the release row is an integrity error (releases
    are immutable, so this can only mean tampering)."""
    if not approver_principal_id:
        raise CampaignValidationError("approver_principal_id is required")
    async with engine.begin() as conn:
        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).mappings().one_or_none()
        if release is None:
            raise ReleaseNotFound(release_id)
        existing = (
            await conn.execute(
                select(release_approvals).where(
                    release_approvals.c.release_row_id == release.id,
                    release_approvals.c.approver_principal_id == approver_principal_id,
                )
            )
        ).mappings().first()
        if existing is not None:
            if existing.release_content_sha256 != release.release_content_sha256:
                raise ApprovalMismatch(
                    f"existing approval of {release_id!r} references a different "
                    "content hash than the release row"
                )
            return
        await conn.execute(
            release_approvals.insert().values(
                id=uuid.uuid4(),
                release_row_id=release.id,
                approver_principal_id=approver_principal_id,
                release_content_sha256=release.release_content_sha256,
                scope=scope,
                basis=basis,
            )
        )


async def release_is_approved(engine: AsyncEngine, release_row_id) -> bool:
    async with engine.begin() as conn:
        release = (
            await conn.execute(
                select(research_releases).where(research_releases.c.id == release_row_id)
            )
        ).mappings().one_or_none()
        if release is None:
            return False
        approval = (
            await conn.execute(
                select(release_approvals.c.release_content_sha256).where(
                    release_approvals.c.release_row_id == release_row_id
                )
            )
        ).scalars().first()
        return approval == release.release_content_sha256


# --- campaigns ---------------------------------------------------------------


@dataclass
class CampaignRecord:
    campaign_id: uuid.UUID
    chain_id: str
    created: bool


def _validate_target_specs(target_specs: list[dict]) -> None:
    if not isinstance(target_specs, list) or not target_specs:
        raise CampaignValidationError("target_specs must be a non-empty list")
    seen = {}
    for spec in target_specs:
        if not isinstance(spec, dict):
            raise CampaignValidationError("target spec entries must be objects")
        horizon = spec.get("horizon_td")
        spec_id = spec.get("target_spec_id")
        content_sha = spec.get("content_sha256")
        if horizon not in HORIZONS_TD:
            raise CampaignValidationError(f"invalid horizon_td {horizon!r}")
        if not isinstance(spec_id, str) or not spec_id:
            raise CampaignValidationError("target_spec_id is required")
        if not isinstance(content_sha, str) or len(content_sha) != 64:
            raise CampaignValidationError(
                f"target spec {spec_id!r} needs a 64-hex content_sha256"
            )
        if horizon in seen:
            raise CampaignValidationError(f"duplicate target spec for horizon {horizon}")
        seen[horizon] = spec
    missing = set(HORIZONS_TD) - set(seen)
    if missing:
        raise CampaignValidationError(f"missing target specs for horizons {sorted(missing)}")


async def _validate_training_reference(conn, release_manifest: dict) -> None:
    """Verify the release manifest's training-manifest reference against
    registered content (campaign-policy §5: training_manifest_ref + hash)."""
    ref = release_manifest.get("training_manifest_ref")
    if ref is None:
        return
    sha = release_manifest.get("training_manifest_sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise CampaignValidationError(
            "training_manifest_ref requires training_manifest_sha256 "
            "(64 hex chars, campaign-policy §5)"
        )
    tm = (
        await conn.execute(
            select(training_manifests).where(
                training_manifests.c.manifest_id == ref
            )
        )
    ).mappings().one_or_none()
    if tm is None:
        raise CampaignValidationError(
            f"training manifest {ref!r} is not registered"
        )
    if tm.content_sha256 != sha:
        raise CampaignValidationError(
            f"training manifest {ref!r} content hash mismatch: release says "
            f"{sha[:12]}, registered is {tm.content_sha256[:12]}"
        )
    declared_fs = {
        fs.get("feature_set_version") for fs in tm.content.get("feature_sets", [])
    }
    fs = release_manifest.get("feature_set_version")
    if fs is not None and fs not in declared_fs:
        raise CampaignValidationError(
            f"feature_set_version {fs!r} is not declared by training "
            f"manifest {ref!r}"
        )
    versions = {
        m.get("role"): m.get("model_version") for m in tm.content.get("models", [])
    }
    for rel_key, role in (
        ("baseline_version", "baseline"),
        ("quant_model_version", "quant_model"),
    ):
        declared = release_manifest.get(rel_key)
        if declared is not None and versions.get(role) != declared:
            raise CampaignValidationError(
                f"{rel_key} {declared!r} does not match the training "
                f"manifest's {role} model ({versions.get(role)!r})"
            )


async def register_campaign(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    campaign_key: str,
    release_id: str,
    target_specs: list[dict],
    time_protocol_ref: str,
    time_protocol_sha256: str,
    benchmark_security_id: uuid.UUID,
    panel_security_ids: list,
    panel_manifest: dict,
    enabled_sources: list[str],
    fallback_policy: str = PHASE1A_FALLBACK_POLICY,
    primary_metric: str = "d20_paired_brier_delta",
) -> CampaignRecord:
    """Pre-register a campaign. Phase 1A policy: enabled sources are
    exactly baseline + quant_model, no fallback. Phase 1B policy:
    baseline + quant_model + llm_adjusted with the quant fallback. The
    referenced release must carry a matching human approval."""
    _validate_target_specs(target_specs)
    enabled = set(enabled_sources)
    if enabled == set(PHASE1A_ENABLED_SOURCES):
        if fallback_policy != PHASE1A_FALLBACK_POLICY:
            raise CampaignValidationError(
                f"Phase 1A fallback_policy must be {PHASE1A_FALLBACK_POLICY!r}"
            )
    elif enabled == set(PHASE1B_ENABLED_SOURCES):
        if fallback_policy != PHASE1B_FALLBACK_POLICY:
            raise CampaignValidationError(
                f"Phase 1B fallback_policy must be {PHASE1B_FALLBACK_POLICY!r}"
            )
    else:
        raise CampaignValidationError(
            "enabled_sources must be exactly Phase 1A "
            f"{list(PHASE1A_ENABLED_SOURCES)} or Phase 1B "
            f"{list(PHASE1B_ENABLED_SOURCES)} (campaign-policy §3); "
            f"got {sorted(enabled_sources)}"
        )
    if not panel_security_ids:
        raise CampaignValidationError("panel_security_ids must be non-empty")
    if len(set(str(s) for s in panel_security_ids)) != len(panel_security_ids):
        raise CampaignValidationError("panel_security_ids contains duplicates")
    if not isinstance(panel_manifest, dict):
        raise CampaignValidationError("panel_manifest must be an object")
    if len(time_protocol_sha256) != 64:
        raise CampaignValidationError("time_protocol_sha256 must be 64 hex chars")

    panel = [str(s) for s in panel_security_ids]

    # campaign-policy §2.1.1: a panel drawn from a frozen registration
    # must cross-check the source, mapping, frame, quotas, selection and
    # the caller's panel_security_ids — a manifest that declares a
    # registration but does not validate against it is rejected.
    reg_id = panel_manifest.get("panel_registration_id")
    if reg_id is not None:
        from youwei_core.data.panel import validate_panel_registration

        try:
            gate = await validate_panel_registration(
                engine, uuid.UUID(str(reg_id)), panel_security_ids=panel
            )
        except (ValueError, TypeError) as exc:
            raise CampaignValidationError(
                f"panel_registration_id {reg_id!r} is not a valid frozen "
                f"registration: {exc}"
            ) from None
        if not gate["ok"]:
            raise CampaignValidationError(
                "panel registration failed cross-validation: "
                + "; ".join(gate["issues"])
            )

    payload = {
        "release_id": release_id,
        "target_specs": target_specs,
        "time_protocol_ref": time_protocol_ref,
        "time_protocol_sha256": time_protocol_sha256,
        "benchmark_security_id": str(benchmark_security_id),
        "panel_security_ids": panel,
        "panel_manifest": panel_manifest,
        "enabled_sources": sorted(enabled_sources),
        "fallback_policy": fallback_policy,
        "primary_metric": primary_metric,
    }
    payload_sha = sha256_hex(payload)

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(campaigns).where(
                    campaigns.c.tenant_id == tenant_id,
                    campaigns.c.campaign_key == campaign_key,
                )
            )
        ).mappings().first()
        if existing is not None:
            if existing.panel_manifest.get("payload_sha256") != payload_sha:
                raise CampaignConflict(
                    f"campaign {campaign_key!r} already exists with different content"
                )
            return CampaignRecord(
                campaign_id=existing.id,
                chain_id=f"campaign:{existing.id}",
                created=False,
            )

        release = (
            await conn.execute(
                select(research_releases).where(
                    research_releases.c.release_id == release_id
                )
            )
        ).mappings().one_or_none()
        if release is None:
            raise ReleaseNotFound(release_id)
        approval = (
            await conn.execute(
                select(release_approvals.c.release_content_sha256).where(
                    release_approvals.c.release_row_id == release.id
                )
            )
        ).scalars().first()
        if approval != release.release_content_sha256:
            raise CampaignValidationError(
                f"release {release_id!r} has no approval matching its content "
                "hash; a campaign needs a human-approved release "
                "(campaign-policy §5)"
            )

        # campaign-policy §5: a release manifest referencing a training
        # manifest must reference REGISTERED content — the ref+hash pair
        # resolves, and the declared feature set and model versions are
        # consistent with it. Releases without the reference register as
        # before (the Phase 1A vehicles disclose their facts in their own
        # registered manifest).
        await _validate_training_reference(conn, release.manifest)

        # panel and benchmark must be real securities
        for sec_id in [str(benchmark_security_id)] + panel:
            found = (
                await conn.execute(
                    select(securities.c.id).where(securities.c.id == uuid.UUID(sec_id))
                )
            ).scalar_one_or_none()
            if found is None:
                raise CampaignValidationError(f"unknown security {sec_id}")

        campaign_id = uuid.uuid4()
        chain_id = f"campaign:{campaign_id}"
        stored_manifest = dict(panel_manifest)
        stored_manifest["payload_sha256"] = payload_sha
        await conn.execute(
            campaigns.insert().values(
                id=campaign_id,
                tenant_id=tenant_id,
                campaign_key=campaign_key,
                release_row_id=release.id,
                target_specs=target_specs,
                time_protocol_ref=time_protocol_ref,
                time_protocol_sha256=time_protocol_sha256,
                benchmark_security_id=benchmark_security_id,
                panel_security_ids=panel,
                panel_manifest=stored_manifest,
                enabled_sources=sorted(enabled_sources),
                fallback_policy=fallback_policy,
                primary_metric=primary_metric,
            )
        )
        await conn.execute(
            ledger_chains.insert().values(chain_id=chain_id, head_seq=0, head_hash=GENESIS_HASH)
        )
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                event_type="campaign.registered",
                payload={"campaign_id": str(campaign_id), "release_id": release_id},
            )
        )
    return CampaignRecord(campaign_id=campaign_id, chain_id=chain_id, created=True)


# --- batch planning ----------------------------------------------------------


@dataclass
class BatchPlan:
    batch_id: uuid.UUID
    campaign_id: uuid.UUID
    decision_cutoff_utc: datetime
    case_ids: list
    created: bool


async def plan_batch(
    engine: AsyncEngine,
    campaign_id: uuid.UUID,
    *,
    decision_cutoff: datetime,
    backfilled_plan: bool = False,
) -> BatchPlan:
    """Resolve and seal one weekly batch with its planned cases.

    The cutoff must be a Saturday 06:00 ET instant (time-protocol §1);
    entry/deadline/exit times resolve through the versioned calendar.
    Planning a cutoff already in the past requires the explicit
    backfilled_plan flag — the record of a missed week stays in the
    coverage denominator instead of being silently skipped."""
    if decision_cutoff.tzinfo is None:
        raise PlanError("decision_cutoff must be timezone-aware")

    async with engine.begin() as conn:
        campaign = (
            await conn.execute(
                select(campaigns).where(campaigns.c.id == campaign_id)
            )
        ).mappings().one_or_none()
        if campaign is None:
            raise CampaignNotFound(str(campaign_id))
        if campaign.status != "active":
            raise PlanError(f"campaign {campaign_id} is not active")

    times: BatchTimes = await resolve_batch_times(engine, decision_cutoff)

    # exits: entry day counts as D1 (target-spec §2)
    exits = {}
    for horizon in HORIZONS_TD:
        exit_date = await trading_day_offset(engine, times.entry_date, horizon)
        exit_session = await session_times(engine, exit_date)
        exits[horizon] = exit_session.close_utc
    spec_by_horizon = {int(s["horizon_td"]): s for s in campaign.target_specs}

    async with engine.begin() as conn:
        db_now = (await conn.execute(select(func.now()))).scalar_one()

        existing = (
            await conn.execute(
                select(forecast_batches).where(
                    forecast_batches.c.campaign_id == campaign_id,
                    forecast_batches.c.decision_cutoff_utc == times.decision_cutoff_utc,
                )
            )
        ).mappings().first()
        if existing is not None:
            case_ids = (
                await conn.execute(
                    select(forecast_cases.c.id)
                    .where(forecast_cases.c.batch_id == existing.id)
                    .order_by(forecast_cases.c.security_id, forecast_cases.c.horizon_td)
                )
            ).scalars().all()
            return BatchPlan(
                batch_id=existing.id,
                campaign_id=campaign_id,
                decision_cutoff_utc=times.decision_cutoff_utc,
                case_ids=list(case_ids),
                created=False,
            )

        if times.decision_cutoff_utc <= db_now and not backfilled_plan:
            raise PlanBackfillRequired(
                f"cutoff {times.decision_cutoff_utc.isoformat()} is not in the "
                "future; planning it is a backfilled record of a missed week "
                "(set backfilled_plan=True)"
            )

        batch_id = uuid.uuid4()
        await conn.execute(
            forecast_batches.insert().values(
                id=batch_id,
                campaign_id=campaign_id,
                decision_cutoff_utc=times.decision_cutoff_utc,
                prediction_deadline_utc=times.prediction_deadline_utc,
                entry_date=times.entry_date,
                entry_at_utc=times.entry_at_utc,
                batch_manifest=times.manifest(),
                backfilled_plan=backfilled_plan,
            )
        )
        case_ids = []
        for sec_id in campaign.panel_security_ids:
            for horizon in HORIZONS_TD:
                spec = spec_by_horizon[horizon]
                case_id = uuid.uuid4()
                case_ids.append(case_id)
                await conn.execute(
                    forecast_cases.insert().values(
                        id=case_id,
                        batch_id=batch_id,
                        campaign_id=campaign_id,
                        security_id=uuid.UUID(sec_id),
                        benchmark_security_id=campaign.benchmark_security_id,
                        horizon_td=horizon,
                        target_spec_id=spec["target_spec_id"],
                        target_spec_sha256=spec["content_sha256"],
                        decision_cutoff_utc=times.decision_cutoff_utc,
                        prediction_deadline_utc=times.prediction_deadline_utc,
                        entry_at_utc=times.entry_at_utc,
                        exit_at_utc=exits[horizon],
                    )
                )
        await conn.execute(
            events.insert().values(
                tenant_id=campaign.tenant_id,
                event_type="batch.planned",
                payload={
                    "campaign_id": str(campaign_id),
                    "batch_id": str(batch_id),
                    "decision_cutoff_utc": times.decision_cutoff_utc.isoformat(),
                    "backfilled_plan": backfilled_plan,
                },
            )
        )
    return BatchPlan(
        batch_id=batch_id,
        campaign_id=campaign_id,
        decision_cutoff_utc=times.decision_cutoff_utc,
        case_ids=case_ids,
        created=True,
    )


async def record_batch_miss(
    engine: AsyncEngine, batch_id: uuid.UUID, *, reason: str
) -> None:
    """Append a missed/skipped fact for a planned batch (time-protocol
    §2: missed weeks stay in the coverage denominator with a recorded
    reason; they are never deleted from the plan)."""
    async with engine.begin() as conn:
        batch = (
            await conn.execute(
                select(forecast_batches).where(forecast_batches.c.id == batch_id)
            )
        ).mappings().one_or_none()
        if batch is None:
            raise PlanError(f"batch {batch_id} not found")
        tenant_id = (
            await conn.execute(
                select(campaigns.c.tenant_id).where(campaigns.c.id == batch.campaign_id)
            )
        ).scalar_one()
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                event_type="batch.missed",
                payload={"batch_id": str(batch_id), "reason": reason[:500]},
            )
        )
