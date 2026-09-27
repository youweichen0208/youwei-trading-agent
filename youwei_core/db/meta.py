"""Database schema (SQLAlchemy Core metadata).

This metadata is the single source of truth for tables; the Alembic
migration in migrations/versions/0001 mirrors it. Money amounts are
integer micros (micro-USD). The events table doubles as the
transactional outbox (architecture section 9): rows are appended in the
same transaction as state changes; published_at marks delivery.
"""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

meta = MetaData(naming_convention=naming_convention)

RUN_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "expired")
ATTEMPT_STATUSES = (
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
    "late",
)
BUDGET_ENTRY_TYPES = ("reserve", "release", "settle", "adjust")

# --- S05: campaign registration & forecast ledger ------------------------

# campaign-policy §3: Phase 1A has no fallback; a failed enabled
# source is sealed as unavailable + reason.
PREDICTION_SOURCES = ("baseline", "quant_model", "llm_adjusted")
SOURCE_STATUSES = ("produced", "fallback", "unavailable")
HORIZONS_TD = (1, 20, 60)

# target-spec §5: status assignments are themselves versioned —
# later evidence appends a new revision, it never edits an old one.
OUTCOME_STATUSES = ("resolved", "unresolved", "unscorable")

# campaign-policy §4: the registered primary horizon.
PRIMARY_HORIZON_TD = 20

# S03: validated artifact kinds are text formats only in MVP; the
# content hash survives a later move to object storage.
ARTIFACT_EXTENSIONS = (".json", ".csv", ".txt", ".md")

# Append-only enforcement (architecture section 6: the application
# cannot UPDATE/DELETE/TRUNCATE ledger records) lives in the Alembic
# migration as BEFORE UPDATE/DELETE/TRUNCATE triggers raising unless
# the session sets youwei.ledger_mutation='on' (ops/test escape
# hatch). meta.py cannot express triggers; the real migrations are
# exercised by every test run.

tenants = Table(
    "tenants",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("slug", Text, nullable=False, unique=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

# API keys: bearer credentials mapping a principal to a tenant.
# Raw tokens are shown once at creation; only the sha256 hash is
# stored. Admin keys (role='admin') manage tenants and keys.
api_keys = Table(
    "api_keys",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False, server_default="tenant"),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("token_prefix", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("revoked_at", TIMESTAMP(timezone=True), nullable=True),
    CheckConstraint("role IN ('tenant', 'admin')", name="role_valid"),
)

runs = Table(
    "runs",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("idempotency_payload_sha256", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("total_budget_micros", BigInteger, nullable=False),
    Column("reserved_micros", BigInteger, nullable=False, server_default="0"),
    Column("settled_micros", BigInteger, nullable=False, server_default="0"),
    Column("wall_clock_deadline", TIMESTAMP(timezone=True), nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_runs_tenant_idem"),
    CheckConstraint("total_budget_micros >= 0", name="total_nonneg"),
    CheckConstraint("reserved_micros >= 0", name="reserved_nonneg"),
    CheckConstraint("settled_micros >= 0", name="settled_nonneg"),
)

jobs = Table(
    "jobs",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("max_attempts", Integer, nullable=False, server_default="3"),
    # attempt_count doubles as the current fencing token for the job:
    # business writes from an attempt must match it to apply.
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_jobs_claim", "status", "created_at"),
)

attempts = Table(
    "attempts",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("job_id", UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False),
    # attempt_no is the fencing token: strictly increasing per job.
    Column("attempt_no", Integer, nullable=False),
    Column("worker_id", Text, nullable=False),
    Column("lease_expires_at", TIMESTAMP(timezone=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("result", JSONB, nullable=True),
    Column("error", Text, nullable=True),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", TIMESTAMP(timezone=True), nullable=True),
    UniqueConstraint("job_id", "attempt_no", name="uq_attempts_job_no"),
)

# events doubles as the transactional outbox (architecture section 9):
# appended in the same transaction as state changes; published_at set by
# the publisher marks delivery. seq is the cursor for downstream mirrors.
events = Table(
    "events",
    meta,
    Column("seq", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=True),
    Column("job_id", UUID(as_uuid=True), nullable=True),
    Column("event_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default="{}"),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("published_at", TIMESTAMP(timezone=True), nullable=True),
    Index("ix_events_tenant_seq", "tenant_id", "seq"),
)

# Budget entries are an append-only ledger per run.
# - reserve: amount reserved before a call/retry is sent upstream
# - settle: actual cost booked after the response is priced
# - release: reservation returned without a call result (cancel path)
# - adjust: manual correction (e.g. reconciled unknown cost)
# An unmatched reserve after its attempt ended is a pending
# reconciliation (timeout/cancel with unknown cost).
budget_entries = Table(
    "budget_entries",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=True),
    Column("entry_type", Text, nullable=False),
    Column("amount_micros", BigInteger, nullable=False),
    # idem_key examples: "{attempt_id}:{call_no}:reserve" / ":settle"
    Column("idem_key", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("run_id", "idem_key", name="uq_budget_run_idem"),
    CheckConstraint("amount_micros >= 0", name="amount_nonneg"),
    Index("ix_budget_run_type", "run_id", "entry_type"),
)

# --- S04: PIT data foundation -------------------------------------------

# Source registry: every raw object and observation points here for
# vendor version and license/authorization tags.
data_sources = Table(
    "data_sources",
    meta,
    Column("id", Text, primary_key=True),  # slug, e.g. 'tiingo'
    Column("name", Text, nullable=False),
    Column("vendor_version", Text, nullable=False),
    Column("license_tags", JSONB, nullable=False, server_default="[]"),
    Column("config", JSONB, nullable=False, server_default="{}"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

# Permanent security identity. Tickers are NOT identities: a ticker
# is a reusable name (SGEN lesson 2026-09-27: a delisted security can
# keep a fresh-looking endDate with phantom volume-0 rows). The
# security id is permanent; names live in security_identities with
# validity ranges.
securities = Table(
    "securities",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("asset_class", Text, nullable=False, server_default="equity"),
    Column("name", Text, nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("asset_class IN ('equity', 'etf')", name="asset_class_valid"),
)

security_identities = Table(
    "security_identities",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("security_id", UUID(as_uuid=True), ForeignKey("securities.id"), nullable=False),
    Column("identifier_type", Text, nullable=False),
    Column("identifier", Text, nullable=False),
    Column("venue", Text, nullable=False, server_default="US"),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date, nullable=True),  # NULL = still valid
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "identifier_type IN ('ticker', 'perma_ticker', 'cusip', 'isin', 'figi')",
        name="id_type_valid",
    ),
    UniqueConstraint(
        "identifier_type", "identifier", "venue", "valid_from",
        name="uq_identities_id_from",
    ),
    Index("ix_identities_lookup", "identifier_type", "identifier", "venue"),
)

# Immutable raw vendor responses: the evidence layer. Every parsed
# observation points back to exactly one raw object (its version).
# Content-level dedup: re-receiving identical bytes for the same
# query is the same object; a vendor correction is new content and
# therefore a new PIT version. source_available_at stays NULL when
# the vendor exposes no timestamp (Tiingo daily prices) — then
# usable_at (>= ingested_at) is the conservative availability time.
raw_objects = Table(
    "raw_objects",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("source_id", Text, ForeignKey("data_sources.id"), nullable=False),
    Column("endpoint", Text, nullable=False),  # e.g. 'daily_prices'
    Column("query", JSONB, nullable=False),
    Column("query_sha256", Text, nullable=False),
    Column("content", Text, nullable=False),  # exact response body
    Column("content_sha256", Text, nullable=False),
    Column("content_type", Text, nullable=False, server_default="application/json"),
    Column("row_count", Integer, nullable=False),
    Column("parser_version", Text, nullable=False),
    Column("source_available_at", TIMESTAMP(timezone=True), nullable=True),
    Column("source_available_basis", Text, nullable=False),
    Column("ingested_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("usable_at", TIMESTAMP(timezone=True), nullable=False),
    UniqueConstraint(
        "source_id", "endpoint", "query_sha256", "content_sha256",
        name="uq_raw_content",
    ),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    Index("ix_raw_source_endpoint", "source_id", "endpoint"),
)

# Parsed daily bars. Version = raw_object_id: a (security, trade_date)
# can carry multiple versions over time; PIT queries pick the latest
# version whose usable_at <= the as-of time, so backfills and vendor
# corrections never change what an older as-of would have returned.
# adj_close is cross-check only (verified ~1e-5 vendor adjustment
# drift); total returns are computed from raw OHLC + div_cash +
# split_factor per target-spec. quality='zero_volume' marks the
# phantom-row signature found on some delisted securities.
price_observations = Table(
    "price_observations",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("raw_object_id", UUID(as_uuid=True), ForeignKey("raw_objects.id"), nullable=False),
    Column("source_id", Text, ForeignKey("data_sources.id"), nullable=False),
    Column("security_id", UUID(as_uuid=True), ForeignKey("securities.id"), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("open", Numeric(20, 8), nullable=False),
    Column("high", Numeric(20, 8), nullable=False),
    Column("low", Numeric(20, 8), nullable=False),
    Column("close", Numeric(20, 8), nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("adj_close", Numeric(20, 8), nullable=True),
    Column("div_cash", Numeric(12, 6), nullable=False),
    Column("split_factor", Numeric(10, 4), nullable=False),
    Column("quality", Text, nullable=False, server_default="ok"),
    CheckConstraint("quality IN ('ok', 'zero_volume')", name="quality_valid"),
    UniqueConstraint("raw_object_id", "trade_date", name="uq_obs_raw_date"),
    Index("ix_obs_pit", "security_id", "trade_date"),
)

# --- S04b: trading calendar + frozen snapshots ---------------------------

# Versioned calendar builds: append-only registry of what the rules
# produced (full canonical day list + hash). time-protocol §5: sealed
# cases keep their planned calendar; manifests record version+hash.
calendar_builds = Table(
    "calendar_builds",
    meta,
    Column("version", Text, primary_key=True),
    Column("venue", Text, nullable=False),
    Column("year_start", Integer, nullable=False),
    Column("year_end", Integer, nullable=False),
    Column("rules_version", Text, nullable=False),
    Column("content", Text, nullable=False),  # canonical JSON of all days
    Column("content_sha256", Text, nullable=False),
    Column("day_count", Integer, nullable=False),
    Column("special_closures", JSONB, nullable=False, server_default="[]"),
    Column("generated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
)

# Materialized per-venue day index (rebuildable): one row per WEEKDAY
# in the built range (trading and non-trading; weekends are implied
# absent). A weekday without a row means the range was never built —
# queries treat that as an error, never as a holiday guess.
calendar_days = Table(
    "calendar_days",
    meta,
    Column("venue", Text, nullable=False),
    Column("date", Date, nullable=False),
    Column("is_trading", Boolean, nullable=False),
    Column("early_close", Boolean, nullable=False, server_default="false"),
    Column("note", Text, nullable=True),  # holiday name / closure reason
    Column("build_version", Text, ForeignKey("calendar_builds.version"), nullable=False),
    PrimaryKeyConstraint("venue", "date"),
    CheckConstraint("is_trading OR NOT early_close", name="early_implies_trading"),
    Index("ix_calendar_trading", "venue", "date", "is_trading"),
)

# Frozen evidence snapshots (architecture section 4): a materialized,
# content-addressed selection of PIT data plus its manifest. Content
# is stored inline in MVP; the hash contract survives a later move to
# object storage. Append-only: no updates, no deletes; identical
# (kind, query, content) re-freezes return the existing snapshot.
snapshots = Table(
    "snapshots",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("kind", Text, nullable=False),  # e.g. 'daily_bars'
    Column("query", JSONB, nullable=False),
    Column("query_sha256", Text, nullable=False),
    Column("as_of", TIMESTAMP(timezone=True), nullable=False),
    Column("mode", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("content", Text, nullable=False),  # canonical JSON
    Column("content_sha256", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("created_by_attempt", UUID(as_uuid=True), nullable=True),
    CheckConstraint("mode IN ('forward', 'historical_source')", name="mode_valid"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    UniqueConstraint("kind", "query_sha256", "content_sha256", name="uq_snapshots_query_content"),
    Index("ix_snapshots_kind", "kind", "created_at"),
)

# --- S05: research releases, campaigns, batches, cases, ledger -----------

# Immutable research release manifests (campaign-policy §5). The
# content hash covers the canonical manifest excluding itself and the
# approval records. Human approval is recorded separately; the Agent
# never is the approving principal.
research_releases = Table(
    "research_releases",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("release_id", Text, nullable=False, unique=True),  # operator slug
    Column("manifest", JSONB, nullable=False),
    Column("release_content_sha256", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("release_content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
)

# Training manifests (S04, campaign-policy §5): FIX the feature sets
# a model combination may consume (with explicit dependencies and
# missing-data policies), per-model preprocessing, label maturation
# rules, fitting window, calibration and model artifacts (content
# hashed). A ResearchRelease references it (training_manifest_ref +
# sha256) and campaign registration verifies the pair resolves to
# registered content. Immutable registration like research_releases.
training_manifests = Table(
    "training_manifests",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("manifest_id", Text, nullable=False, unique=True),  # operator slug
    Column("content", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
)

# Human approval of a release content hash. Unique per (release,
# approver); campaign registration requires at least one approval
# whose hash still matches the release row.
release_approvals = Table(
    "release_approvals",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("release_row_id", UUID(as_uuid=True), ForeignKey("research_releases.id"), nullable=False),
    Column("approver_principal_id", Text, nullable=False),
    Column("approved_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("release_content_sha256", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("basis", Text, nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("release_row_id", "approver_principal_id", name="uq_approvals_release_approver"),
)

# Pre-registered multi-week research plan (campaign-policy §2.3):
# fixes panel, target specs, enabled sources, frequency and release.
# Immutable after creation; lifecycle facts (closure) are events.
campaigns = Table(
    "campaigns",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("campaign_key", Text, nullable=False),
    Column("release_row_id", UUID(as_uuid=True), ForeignKey("research_releases.id"), nullable=False),
    # [{horizon_td, target_spec_id, content_sha256}] covering 1/20/60
    Column("target_specs", JSONB, nullable=False),
    Column("time_protocol_ref", Text, nullable=False),
    Column("time_protocol_sha256", Text, nullable=False),
    Column("benchmark_security_id", UUID(as_uuid=True), ForeignKey("securities.id"), nullable=False),
    # ordered list of permanent security ids (the fixed panel)
    Column("panel_security_ids", JSONB, nullable=False),
    Column("panel_manifest", JSONB, nullable=False),
    Column("enabled_sources", JSONB, nullable=False),
    Column("fallback_policy", Text, nullable=False),
    Column("primary_metric", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "campaign_key", name="uq_campaigns_tenant_key"),
    CheckConstraint("time_protocol_sha256 ~ '^[0-9a-f]{64}$'", name="time_sha_format"),
)

# One decision_cutoff's weekly instance (time-protocol §1). Planned
# times are sealed here before the batch starts; a cutoff already in
# the past may only be planned as an explicit backfilled record of a
# missed week (backfilled_plan=true).
forecast_batches = Table(
    "forecast_batches",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False),
    Column("decision_cutoff_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("prediction_deadline_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("entry_date", Date, nullable=False),
    Column("entry_at_utc", TIMESTAMP(timezone=True), nullable=False),
    # full BatchTimes manifest incl. calendar version/hash and tzdb
    Column("batch_manifest", JSONB, nullable=False),
    Column("backfilled_plan", Boolean, nullable=False, server_default="false"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("campaign_id", "decision_cutoff_utc", name="uq_batches_campaign_cutoff"),
    Index("ix_batches_campaign", "campaign_id", "decision_cutoff_utc"),
)

# The planned prediction question: batch x security x horizon with
# sealed instance windows (architecture section 5). Cases stay in the
# coverage denominator whether they fail, are missed or go unscorable.
forecast_cases = Table(
    "forecast_cases",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("batch_id", UUID(as_uuid=True), ForeignKey("forecast_batches.id"), nullable=False),
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False),
    Column("security_id", UUID(as_uuid=True), ForeignKey("securities.id"), nullable=False),
    Column("benchmark_security_id", UUID(as_uuid=True), nullable=False),
    Column("horizon_td", Integer, nullable=False),
    Column("target_spec_id", Text, nullable=False),
    Column("target_spec_sha256", Text, nullable=False),
    Column("decision_cutoff_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("prediction_deadline_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("entry_at_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("exit_at_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("batch_id", "security_id", "horizon_td", name="uq_cases_batch_sec_horizon"),
    CheckConstraint("horizon_td IN (1, 20, 60)", name="horizon_valid"),
    CheckConstraint("target_spec_sha256 ~ '^[0-9a-f]{64}$'", name="spec_sha_format"),
    Index("ix_cases_campaign", "campaign_id"),
)

# Hash-chain head per campaign; the row the sealing transaction
# locks (FOR UPDATE) to serialize commits and advance the chain.
# Mutable by design: it is lock state, not a ledger record.
ledger_chains = Table(
    "ledger_chains",
    meta,
    Column("chain_id", Text, primary_key=True),  # f"campaign:{campaign_id}"
    Column("head_seq", BigInteger, nullable=False, server_default="0"),
    Column("head_hash", Text, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

# Atomic seal of one case's three source positions for one release
# (architecture section 3.4). sealed_at is clock_timestamp() read
# after the chain lock; timeliness is NOT a mutable column — durable
# confirmations append to forecast_commit_events and the current
# judgment is derived (unconfirmed -> uncertain once past deadline).
forecast_commits = Table(
    "forecast_commits",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("case_id", UUID(as_uuid=True), ForeignKey("forecast_cases.id"), nullable=False),
    Column("release_row_id", UUID(as_uuid=True), ForeignKey("research_releases.id"), nullable=False),
    Column("chain_id", Text, nullable=False),
    Column("chain_seq", BigInteger, nullable=False),
    Column("prev_hash", Text, nullable=False),
    # payload hash: idempotency over business content (no chain/time
    # fields); content hash: full chained record hash
    Column("payload_sha256", Text, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("sealed_at", TIMESTAMP(timezone=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=True),
    Column("attempt_no", Integer, nullable=True),
    Column("input_manifest", JSONB, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("case_id", "release_row_id", name="uq_commits_case_release"),
    UniqueConstraint("chain_id", "chain_seq", name="uq_commits_chain_seq"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_sha_format"),
    Index("ix_commits_case", "case_id"),
)

# The three source positions of one commit (campaign-policy §3):
# produced / fallback / unavailable with value-range discipline —
# unavailable carries NULL values + reason, never a fake probability.
predictions = Table(
    "predictions",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("commit_id", UUID(as_uuid=True), ForeignKey("forecast_commits.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("source_status", Text, nullable=False),
    Column("reason", Text, nullable=True),
    Column("p_outperform", Numeric(11, 10), nullable=True),
    Column("expected_excess_return", Numeric(20, 10), nullable=True),
    Column("evidence_snapshot_id", UUID(as_uuid=True), ForeignKey("snapshots.id"), nullable=True),
    Column("model_version", Text, nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("commit_id", "source", name="uq_predictions_commit_source"),
    CheckConstraint("source IN ('baseline', 'quant_model', 'llm_adjusted')", name="source_valid"),
    CheckConstraint(
        "source_status IN ('produced', 'fallback', 'unavailable')", name="status_valid"
    ),
    CheckConstraint("p_outperform IS NULL OR (p_outperform >= 0 AND p_outperform <= 1)", name="p_range"),
    CheckConstraint(
        "source_status = 'unavailable' OR p_outperform IS NOT NULL", name="produced_has_p"
    ),
    CheckConstraint(
        "source_status <> 'unavailable' OR (p_outperform IS NULL AND expected_excess_return IS NULL)",
        name="unavailable_has_no_values",
    ),
    # evidence-consuming models cannot seal produced positions without
    # the frozen snapshot they were computed from (S05e traceability)
    CheckConstraint(
        "source NOT IN ('quant_model', 'llm_adjusted') OR source_status <> 'produced' "
        "OR evidence_snapshot_id IS NOT NULL",
        name="produced_evidence_required",
    ),
)

# Appended durable confirmations and timeliness judgments
# (time-protocol §4). At most one durable_confirmation per commit
# (partial unique index); a commit without one is unconfirmed, and
# conservatively uncertain once its deadline has passed.
forecast_commit_events = Table(
    "forecast_commit_events",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("commit_id", UUID(as_uuid=True), ForeignKey("forecast_commits.id"), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("payload", JSONB, nullable=False, server_default="{}"),
    Index("ix_commit_events_commit", "commit_id", "occurred_at"),
    Index(
        "uq_commit_events_confirmation",
        "commit_id",
        unique=True,
        postgresql_where=text("event_type = 'durable_confirmation'"),
    ),
)

# Append-only outcome revisions per case (architecture section 6,
# Outcome示意): unique (case_id, revision); each revision supersedes
# the previous head under the case lock — no forks, no overwrites.
# resolved rows carry all three returns; unresolved/unscorable rows
# carry none (never a fabricated number). The referenced frozen
# snapshot is exactly what the resolver computed from.
outcome_revisions = Table(
    "outcome_revisions",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("case_id", UUID(as_uuid=True), ForeignKey("forecast_cases.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("supersedes_outcome_id", UUID(as_uuid=True), ForeignKey("outcome_revisions.id"), nullable=True),
    Column("status", Text, nullable=False),
    Column("recorded_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("entry_at_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("exit_at_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("asset_return", Numeric(20, 10), nullable=True),
    Column("benchmark_return", Numeric(20, 10), nullable=True),
    Column("excess_return", Numeric(20, 10), nullable=True),
    Column("prices_and_actions_snapshot_id", UUID(as_uuid=True), ForeignKey("snapshots.id"), nullable=True),
    Column("resolver_version", Text, nullable=False),
    Column("correction_reason", Text, nullable=True),
    Column("basis", JSONB, nullable=False, server_default="{}"),
    UniqueConstraint("case_id", "revision", name="uq_outcomes_case_revision"),
    CheckConstraint("status IN ('resolved', 'unresolved', 'unscorable')", name="status_valid"),
    CheckConstraint(
        "(status = 'resolved') = (asset_return IS NOT NULL AND benchmark_return IS NOT NULL AND excess_return IS NOT NULL)",
        name="resolved_iff_values",
    ),
    CheckConstraint(
        "(revision = 1) = (supersedes_outcome_id IS NULL)",
        name="first_revision_no_parent",
    ),
    Index("ix_outcomes_case", "case_id", "revision"),
)

# Batch x horizon evaluation reports (architecture section 6,
# campaign-policy §4): the report FIXES the case set, the commit and
# outcome-revision references it scored, the release and the scoring
# code version. Corrections append new versions (supersedes chain);
# old reports keep their original references. Content is canonical
# JSON with a content hash: identical heads regenerate identically.
evaluation_reports = Table(
    "evaluation_reports",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("batch_id", UUID(as_uuid=True), ForeignKey("forecast_batches.id"), nullable=False),
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False),
    Column("horizon_td", Integer, nullable=False),
    Column("report_version", Integer, nullable=False),
    Column("supersedes_report_id", UUID(as_uuid=True), ForeignKey("evaluation_reports.id"), nullable=True),
    Column("release_row_id", UUID(as_uuid=True), ForeignKey("research_releases.id"), nullable=False),
    Column("scoring_code_version", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("batch_id", "horizon_td", "report_version", name="uq_eval_batch_horizon_version"),
    CheckConstraint("horizon_td IN (1, 20, 60)", name="horizon_valid"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    CheckConstraint(
        "(report_version = 1) = (supersedes_report_id IS NULL)",
        name="first_version_no_parent",
    ),
    Index("ix_eval_reports_batch", "batch_id", "horizon_td", "report_version"),
)

# Sandbox execution artifacts (architecture §10): outputs of untrusted
# code, validated before storage — whitelist extensions, size/count
# caps, no symlinks/hardlinks/traversal, hashed, bound to the fenced
# attempt that produced them. Content is inline text in MVP; the hash
# contract survives a later move to object storage. Append-only.
artifacts = Table(
    "artifacts",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("job_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    # relative POSIX path inside the sandbox /outputs
    Column("path", Text, nullable=False),
    Column("extension", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("job_id", "attempt_no", "path", name="uq_artifacts_job_attempt_path"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    CheckConstraint("size_bytes >= 0", name="size_nonneg"),
    Index("ix_artifacts_job", "job_id"),
    Index("ix_artifacts_tenant", "tenant_id"),
)

# Monthly summary reports (campaign-policy §4.2): batch-equal-weighted
# aggregation of the registered batch D20 point estimates, attributed
# by each batch's cutoff month. Versions append as labels mature or
# get corrected (the scheduled v1 lands on the first regular trading
# day of the next month at 06:00 ET, data cutoff the previous natural
# month end); old versions keep their original references. The report
# fixes the batch-report versions it aggregates plus the month's
# case/commit/outcome-head references.
monthly_summary_reports = Table(
    "monthly_summary_reports",
    meta,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False),
    # first day of the attribution month
    Column("month", Date, nullable=False),
    Column("report_version", Integer, nullable=False),
    Column("supersedes_report_id", UUID(as_uuid=True), ForeignKey("monthly_summary_reports.id"), nullable=True),
    Column("release_row_id", UUID(as_uuid=True), ForeignKey("research_releases.id"), nullable=False),
    # the scoring version of the batch reports being aggregated
    Column("scoring_code_version", Text, nullable=False),
    Column("monthly_code_version", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("campaign_id", "month", "report_version", name="uq_monthly_campaign_month_version"),
    CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha_format"),
    CheckConstraint(
        "(report_version = 1) = (supersedes_report_id IS NULL)",
        name="first_version_no_parent",
    ),
    CheckConstraint("EXTRACT(DAY FROM month) = 1", name="month_first_day"),
    Index("ix_monthly_reports_campaign", "campaign_id", "month", "report_version"),
)

# Regeneration-gating cache: the inputs digest each batch's reports
# were last generated from (cases, outcome heads, commits, durable
# confirmations, maturity). NOT a ledger record — derived, mutable,
# safe to truncate and rebuild; the reports themselves stay
# append-only in evaluation_reports.
batch_report_input_state = Table(
    "batch_report_input_state",
    meta,
    Column("batch_id", UUID(as_uuid=True), ForeignKey("forecast_batches.id"), primary_key=True),
    Column("inputs_sha256", Text, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("inputs_sha256 ~ '^[0-9a-f]{64}$'", name="inputs_sha_format"),
)

# Same gating cache for monthly summaries: keyed by (campaign, month).
monthly_report_input_state = Table(
    "monthly_report_input_state",
    meta,
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id"), primary_key=True),
    Column("month", Date, nullable=False, primary_key=True),
    Column("inputs_sha256", Text, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("inputs_sha256 ~ '^[0-9a-f]{64}$'", name="inputs_sha_format"),
    CheckConstraint("EXTRACT(DAY FROM month) = 1", name="month_first_day"),
)
