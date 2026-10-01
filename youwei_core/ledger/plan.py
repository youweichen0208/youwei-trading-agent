"""Campaign execution plan: frozen weekly cutoffs, plan hash and approval scope.

S06i. A campaign no longer plans batches without bound: the registration
freezes the exact weekly cutoff list (expanded from ``first_cutoff`` +
``batch_count`` in America/New_York, never by adding 168h in UTC, which would
drift across DST). Two hashes are distinguished:

- ``planned_cutoffs_sha256``: the concrete weekly cutoff list.
- ``campaign_plan_sha256``: the FULL experiment scope (tenant, campaign key,
  release hash, panel, target/benchmark, sources, fallback, primary metric,
  time protocol and the cutoff list) so a release approval bound to this plan
  cannot be re-applied to a different panel or model.

The approval ``scope_manifest`` binds ``campaign_plan_sha256`` (not merely the
release hash). A legacy free-text ``scope`` stays for audit but grants no new
experiment authorization.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from youwei_core.data.calendar import ET

SATURDAY = 5  # Monday=0 .. Sunday=6
CUTOFF_TIME_ET = time(6, 0)
SCOPE_SCHEMA_VERSION = 1


def _canonical_json(obj) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(obj) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def expand_planned_cutoffs(first_cutoff: datetime, batch_count: int) -> list[datetime]:
    """Expand ``batch_count`` consecutive Saturday-06:00-ET cutoffs.

    Each next cutoff advances the America/New_York local date by 7 days and
    keeps the 06:00 wall-clock, then converts to UTC. Advancing in UTC by
    ``timedelta(hours=168)`` would drift an hour across DST boundaries, so the
    local date is advanced instead.
    """
    if first_cutoff.tzinfo is None or first_cutoff.utcoffset() is None:
        raise ValueError("first_cutoff must be timezone-aware")
    if type(batch_count) is not int or batch_count < 1:
        raise ValueError("batch_count must be a positive integer")
    local = first_cutoff.astimezone(ET)
    if local.weekday() != SATURDAY or (local.hour, local.minute, local.second, local.microsecond) != (6, 0, 0, 0):
        raise ValueError("first_cutoff must be Saturday 06:00 America/New_York")
    cutoffs = []
    current_local_date = local.date()
    for _ in range(batch_count):
        cutoff_et = datetime.combine(current_local_date, CUTOFF_TIME_ET, tzinfo=ET)
        cutoffs.append(cutoff_et.astimezone(UTC))
        current_local_date += timedelta(days=7)
    return cutoffs


def _utc_iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat()


class CampaignPlanScope(BaseModel):
    """The structured approval scope (S06i §2). Binds a release approval to
    one concrete experiment plan, not just the release content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCOPE_SCHEMA_VERSION, frozen=True)
    kind: str = Field(default="campaign_execution", frozen=True)
    phase: str
    tenant_id: uuid.UUID
    campaign_key: str
    release_content_sha256: str
    campaign_plan_sha256: str

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value):
        if value != SCOPE_SCHEMA_VERSION:
            raise ValueError("unsupported scope schema_version")
        return value

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        if value != "campaign_execution":
            raise ValueError("scope kind must be campaign_execution")
        return value

    @field_validator("phase")
    @classmethod
    def _phase(cls, value):
        if value not in ("1a", "1b"):
            raise ValueError("scope phase must be 1a or 1b")
        return value

    @field_validator("release_content_sha256", "campaign_plan_sha256")
    @classmethod
    def _sha(cls, value):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("must be a 64-char hex sha256")
        return value


def scope_manifest_hash(scope: CampaignPlanScope | dict) -> str:
    """Hash over the canonical scope manifest (excludes the hash itself)."""
    if isinstance(scope, CampaignPlanScope):
        payload = scope.model_dump(mode="json")
    else:
        payload = dict(scope)
    return _sha256_hex(payload)


def campaign_plan_sha256_from(
    *,
    tenant_id: uuid.UUID,
    campaign_key: str,
    release_content_sha256: str,
    phase: str,
    panel_security_ids: list[str],
    benchmark_security_id: str,
    target_specs: list[dict],
    enabled_sources: list[str],
    fallback_policy: str,
    primary_metric: str,
    time_protocol_ref: str,
    time_protocol_sha256: str,
    planned_cutoffs: list[str],
) -> str:
    """Recompute the full campaign plan hash from ACTUAL registration params.

    This is the server-side recomputation the S06i ``register_campaign`` gate
    uses to bind the plan hash to the concrete values it is about to persist —
    the caller must NOT be trusted to supply a hash that could stay valid while
    the underlying plan parameters (panel, target, sources, primary metric,
    cutoffs, ...) change. ``planned_cutoffs`` is the already-expanded ISO-8601
    cutoff list (the same bytes ``planned_cutoffs_sha256`` was computed over).

    The field set and canonicalization MUST stay byte-identical to what
    ``prepare_campaign_plan`` hashes, or a plan built by that helper would no
    longer verify here.
    """
    plan_scope = {
        "tenant_id": str(tenant_id),
        "campaign_key": campaign_key,
        "release_content_sha256": release_content_sha256,
        "phase": phase,
        "panel_security_ids": sorted(str(s) for s in panel_security_ids),
        "benchmark_security_id": str(benchmark_security_id),
        "target_specs": sorted(target_specs, key=lambda s: s.get("horizon_td", 0)),
        "enabled_sources": sorted(enabled_sources),
        "fallback_policy": fallback_policy,
        "primary_metric": primary_metric,
        "time_protocol_ref": time_protocol_ref,
        "time_protocol_sha256": time_protocol_sha256,
        "planned_cutoffs": planned_cutoffs,
    }
    return _sha256_hex(plan_scope)


def prepare_campaign_plan(
    *,
    tenant_id: uuid.UUID,
    campaign_key: str,
    release_content_sha256: str,
    phase: str,
    first_cutoff: datetime,
    batch_count: int,
    panel_security_ids: list[str],
    benchmark_security_id: str,
    target_specs: list[dict],
    enabled_sources: list[str],
    fallback_policy: str,
    primary_metric: str,
    time_protocol_ref: str,
    time_protocol_sha256: str,
) -> dict:
    """Build the frozen plan and its two hashes. Pure: no database, no time
    re-read — ``register_campaign`` consumes this instead of regenerating it."""
    planned_cutoffs = expand_planned_cutoffs(first_cutoff, batch_count)
    planned_cutoffs_sha256 = _sha256_hex([_utc_iso(c) for c in planned_cutoffs])

    cutoff_iso = [_utc_iso(c) for c in planned_cutoffs]
    campaign_plan_sha256 = campaign_plan_sha256_from(
        tenant_id=tenant_id,
        campaign_key=campaign_key,
        release_content_sha256=release_content_sha256,
        phase=phase,
        panel_security_ids=[str(s) for s in panel_security_ids],
        benchmark_security_id=str(benchmark_security_id),
        target_specs=target_specs,
        enabled_sources=enabled_sources,
        fallback_policy=fallback_policy,
        primary_metric=primary_metric,
        time_protocol_ref=time_protocol_ref,
        time_protocol_sha256=time_protocol_sha256,
        planned_cutoffs=cutoff_iso,
    )

    return {
        "phase": phase,
        "first_cutoff": _utc_iso(first_cutoff),
        "batch_count": batch_count,
        "planned_cutoffs": cutoff_iso,
        "planned_cutoffs_sha256": planned_cutoffs_sha256,
        "campaign_plan_sha256": campaign_plan_sha256,
        "timezone": "America/New_York",
    }
