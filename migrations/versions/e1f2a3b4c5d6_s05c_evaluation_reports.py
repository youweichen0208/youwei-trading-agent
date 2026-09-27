"""s05c evaluation reports

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-27 19:00:00.000000

Adds the append-only evaluation_reports table (campaign-policy §4):
batch x horizon reports fixing the scored case set, commit and
outcome-revision references, release and scoring code version.
Corrections append new report versions; the table joins the
append-only trigger set.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('evaluation_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('horizon_td', sa.Integer(), nullable=False),
    sa.Column('report_version', sa.Integer(), nullable=False),
    sa.Column('supersedes_report_id', sa.UUID(), nullable=True),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('scoring_code_version', sa.Text(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_evaluation_reports_content_sha_format')),
    sa.CheckConstraint("horizon_td IN (1, 20, 60)", name=op.f('ck_evaluation_reports_horizon_valid')),
    sa.CheckConstraint("(report_version = 1) = (supersedes_report_id IS NULL)", name=op.f('ck_evaluation_reports_first_version_no_parent')),
    sa.ForeignKeyConstraint(['batch_id'], ['forecast_batches.id'], name=op.f('fk_evaluation_reports_batch_id_forecast_batches')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_evaluation_reports_campaign_id_campaigns')),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_evaluation_reports_release_row_id_research_releases')),
    sa.ForeignKeyConstraint(['supersedes_report_id'], ['evaluation_reports.id'], name=op.f('fk_evaluation_reports_supersedes_report_id_evaluation_reports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evaluation_reports')),
    sa.UniqueConstraint('batch_id', 'horizon_td', 'report_version', name=op.f('uq_eval_batch_horizon_version'))
    )
    op.create_index(op.f('ix_eval_reports_batch'), 'evaluation_reports', ['batch_id', 'horizon_td', 'report_version'], unique=False)

    op.execute(
        "CREATE TRIGGER evaluation_reports_no_update BEFORE UPDATE OR DELETE ON evaluation_reports "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER evaluation_reports_no_truncate BEFORE TRUNCATE ON evaluation_reports "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evaluation_reports_no_truncate ON evaluation_reports;")
    op.execute("DROP TRIGGER IF EXISTS evaluation_reports_no_update ON evaluation_reports;")
    op.drop_index(op.f('ix_eval_reports_batch'), table_name='evaluation_reports')
    op.drop_table('evaluation_reports')
