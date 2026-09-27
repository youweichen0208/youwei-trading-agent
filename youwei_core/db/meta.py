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
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
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
