"""s12a exploratory research

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-03 12:00:00.000000

Adds the S12a exploratory research tables (implementation plan S12):

- ``exploratory_research``: one user-initiated research task — its own
  question context (security, benchmark, horizon, registered target
  spec, cutoff/entry/exit resolved through the versioned calendar) plus
  a config manifest and hash frozen at submit time. Mutable task state
  (status transitions), like jobs; never creates Campaign or
  ForecastCase rows.
- ``exploratory_research_reports``: versioned, append-only reports
  (UPDATE/DELETE/TRUNCATE blocked via youwei_ledger_block_mutation).
  Re-runs append a new version only when content differs
  (content_sha256 idempotency); old versions stay readable. Reports
  never enter the prediction ledger.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('exploratory_research',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('security_id', sa.UUID(), nullable=False),
    sa.Column('benchmark_security_id', sa.UUID(), nullable=False),
    sa.Column('horizon_td', sa.Integer(), nullable=False),
    sa.Column('target_spec_id', sa.Text(), nullable=False),
    sa.Column('target_spec_sha256', sa.Text(), nullable=False),
    sa.Column('decision_cutoff_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('prediction_deadline_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('entry_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('exit_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('config_manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('config_sha256', sa.Text(), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','running','succeeded','failed','cancelled')", name=op.f('ck_exploratory_status_valid')),
    sa.CheckConstraint('horizon_td IN (1, 20, 60)', name=op.f('ck_exploratory_horizon_valid')),
    sa.CheckConstraint("target_spec_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_exploratory_spec_sha_format')),
    sa.CheckConstraint("config_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_exploratory_config_sha_format')),
    sa.ForeignKeyConstraint(['benchmark_security_id'], ['securities.id'], name=op.f('fk_exploratory_research_benchmark_security_id_securities')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_exploratory_research_job_id_jobs')),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_exploratory_research_run_id_runs')),
    sa.ForeignKeyConstraint(['security_id'], ['securities.id'], name=op.f('fk_exploratory_research_security_id_securities')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_exploratory_research_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exploratory_research')),
    sa.UniqueConstraint('tenant_id', 'idempotency_key', name=op.f('uq_exploratory_tenant_idem')),
    )
    op.create_index('ix_exploratory_tenant_created', 'exploratory_research', ['tenant_id', 'created_at'], unique=False)

    op.create_table('exploratory_research_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('research_id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('report_version', sa.Integer(), nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('evidence_snapshot_id', sa.UUID(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("attempt_no > 0", name=op.f('ck_exploratory_report_attempt_no_positive')),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_exploratory_report_sha_format')),
    sa.CheckConstraint('report_version > 0', name=op.f('ck_exploratory_report_version_positive')),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], name=op.f('fk_exploratory_research_reports_attempt_id_attempts')),
    sa.ForeignKeyConstraint(['evidence_snapshot_id'], ['snapshots.id'], name=op.f('fk_exploratory_research_reports_evidence_snapshot_id_snapshots')),
    sa.ForeignKeyConstraint(['research_id'], ['exploratory_research.id'], name=op.f('fk_exploratory_research_reports_research_id_exploratory_research')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_exploratory_research_reports_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exploratory_research_reports')),
    sa.UniqueConstraint('research_id', 'report_version', name=op.f('uq_exploratory_report_version')),
    )
    op.create_index('ix_exploratory_reports_research', 'exploratory_research_reports', ['research_id', 'report_version'], unique=False)

    # append-only discipline for reports (the task table itself is
    # mutable state: pending -> running -> succeeded/failed/cancelled)
    op.execute(
        "CREATE TRIGGER exploratory_reports_no_update BEFORE UPDATE OR DELETE ON exploratory_research_reports "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER exploratory_reports_no_truncate BEFORE TRUNCATE ON exploratory_research_reports "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS exploratory_reports_no_truncate ON exploratory_research_reports;")
    op.execute("DROP TRIGGER IF EXISTS exploratory_reports_no_update ON exploratory_research_reports;")
    op.drop_index('ix_exploratory_reports_research', table_name='exploratory_research_reports')
    op.drop_table('exploratory_research_reports')
    op.drop_index('ix_exploratory_tenant_created', table_name='exploratory_research')
    op.drop_table('exploratory_research')
