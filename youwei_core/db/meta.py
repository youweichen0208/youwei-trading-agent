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
    ForeignKey,
    Index,
    Integer,
    MetaData,
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
