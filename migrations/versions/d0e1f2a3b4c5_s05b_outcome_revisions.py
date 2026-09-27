"""s05b outcome revisions

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-27 17:30:00.000000

Adds the append-only outcome_revisions table (target-spec §5): one
current fact per case is the head of a revision chain — every later
arrival of evidence or correction appends a new revision that
supersedes the previous head; nothing is updated or deleted. The
table joins the append-only trigger set.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('outcome_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('supersedes_outcome_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('recorded_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('entry_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('exit_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('asset_return', sa.Numeric(20, 10), nullable=True),
    sa.Column('benchmark_return', sa.Numeric(20, 10), nullable=True),
    sa.Column('excess_return', sa.Numeric(20, 10), nullable=True),
    sa.Column('prices_and_actions_snapshot_id', sa.UUID(), nullable=True),
    sa.Column('resolver_version', sa.Text(), nullable=False),
    sa.Column('correction_reason', sa.Text(), nullable=True),
    sa.Column('basis', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.CheckConstraint("status IN ('resolved', 'unresolved', 'unscorable')", name=op.f('ck_outcome_revisions_status_valid')),
    sa.CheckConstraint("(status = 'resolved') = (asset_return IS NOT NULL AND benchmark_return IS NOT NULL AND excess_return IS NOT NULL)", name=op.f('ck_outcome_revisions_resolved_iff_values')),
    sa.CheckConstraint("(revision = 1) = (supersedes_outcome_id IS NULL)", name=op.f('ck_outcome_revisions_first_revision_no_parent')),
    sa.ForeignKeyConstraint(['case_id'], ['forecast_cases.id'], name=op.f('fk_outcome_revisions_case_id_forecast_cases')),
    sa.ForeignKeyConstraint(['prices_and_actions_snapshot_id'], ['snapshots.id'], name=op.f('fk_outcome_revisions_prices_and_actions_snapshot_id_snapshots')),
    sa.ForeignKeyConstraint(['supersedes_outcome_id'], ['outcome_revisions.id'], name=op.f('fk_outcome_revisions_supersedes_outcome_id_outcome_revisions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outcome_revisions')),
    sa.UniqueConstraint('case_id', 'revision', name=op.f('uq_outcomes_case_revision'))
    )
    op.create_index(op.f('ix_outcomes_case'), 'outcome_revisions', ['case_id', 'revision'], unique=False)

    op.execute(
        "CREATE TRIGGER outcome_revisions_no_update BEFORE UPDATE OR DELETE ON outcome_revisions "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER outcome_revisions_no_truncate BEFORE TRUNCATE ON outcome_revisions "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS outcome_revisions_no_truncate ON outcome_revisions;")
    op.execute("DROP TRIGGER IF EXISTS outcome_revisions_no_update ON outcome_revisions;")
    op.drop_index(op.f('ix_outcomes_case'), table_name='outcome_revisions')
    op.drop_table('outcome_revisions')
