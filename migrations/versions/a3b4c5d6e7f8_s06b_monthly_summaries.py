"""s06b monthly summaries and report regeneration gating

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-28 20:00:00.000000

Adds:
- monthly_summary_reports (campaign-policy §4.2): append-only monthly
  summaries aggregating the registered batch D20 point estimates,
  batch-equal-weighted, attributed by cutoff month; versions append as
  labels mature or get corrected. Joins the append-only trigger set.
- batch_report_input_state / monthly_report_input_state: mutable
  derived caches holding the inputs digest reports were last generated
  from, so the scheduler tick skips regenerations whose inputs are
  unchanged. NOT ledger records — no mutation triggers.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('monthly_summary_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('report_version', sa.Integer(), nullable=False),
    sa.Column('supersedes_report_id', sa.UUID(), nullable=True),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('scoring_code_version', sa.Text(), nullable=False),
    sa.Column('monthly_code_version', sa.Text(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_monthly_summary_reports_content_sha_format')),
    sa.CheckConstraint("EXTRACT(DAY FROM month) = 1", name=op.f('ck_monthly_summary_reports_month_first_day')),
    sa.CheckConstraint("(report_version = 1) = (supersedes_report_id IS NULL)", name=op.f('ck_monthly_summary_reports_first_version_no_parent')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_monthly_summary_reports_campaign_id_campaigns')),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_monthly_summary_reports_release_row_id_research_releases')),
    sa.ForeignKeyConstraint(['supersedes_report_id'], ['monthly_summary_reports.id'], name=op.f('fk_monthly_summary_reports_supersedes_report_id_monthly_summary_reports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_monthly_summary_reports')),
    sa.UniqueConstraint('campaign_id', 'month', 'report_version', name=op.f('uq_monthly_campaign_month_version'))
    )
    op.create_index(op.f('ix_monthly_reports_campaign'), 'monthly_summary_reports', ['campaign_id', 'month', 'report_version'], unique=False)

    op.execute(
        "CREATE TRIGGER monthly_summary_reports_no_update BEFORE UPDATE OR DELETE ON monthly_summary_reports "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER monthly_summary_reports_no_truncate BEFORE TRUNCATE ON monthly_summary_reports "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )

    op.create_table('batch_report_input_state',
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('inputs_sha256', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("inputs_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_batch_report_input_state_inputs_sha_format')),
    sa.ForeignKeyConstraint(['batch_id'], ['forecast_batches.id'], name=op.f('fk_batch_report_input_state_batch_id_forecast_batches')),
    sa.PrimaryKeyConstraint('batch_id', name=op.f('pk_batch_report_input_state'))
    )

    op.create_table('monthly_report_input_state',
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('inputs_sha256', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("EXTRACT(DAY FROM month) = 1", name=op.f('ck_monthly_report_input_state_month_first_day')),
    sa.CheckConstraint("inputs_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_monthly_report_input_state_inputs_sha_format')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_monthly_report_input_state_campaign_id_campaigns')),
    sa.PrimaryKeyConstraint('campaign_id', 'month', name=op.f('pk_monthly_report_input_state'))
    )


def downgrade() -> None:
    op.drop_table('monthly_report_input_state')
    op.drop_table('batch_report_input_state')
    op.execute("DROP TRIGGER IF EXISTS monthly_summary_reports_no_truncate ON monthly_summary_reports;")
    op.execute("DROP TRIGGER IF EXISTS monthly_summary_reports_no_update ON monthly_summary_reports;")
    op.drop_index(op.f('ix_monthly_reports_campaign'), table_name='monthly_summary_reports')
    op.drop_table('monthly_summary_reports')
