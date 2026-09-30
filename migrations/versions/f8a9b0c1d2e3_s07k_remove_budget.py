"""s07k remove budget accounting

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-30 00:00:00.000000

Removes the cost/budget dimension entirely (S07k decision): drop the
budget_entries table and the runs.*_micros columns (total, reserved,
settled, estimated). Run lifecycle, idempotency, leasing and fencing are
unchanged — only the monetary accounting is removed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('budget_entries')
    op.drop_constraint('estimated_nonneg', 'runs', type_='check')
    op.drop_constraint('settled_nonneg', 'runs', type_='check')
    op.drop_constraint('reserved_nonneg', 'runs', type_='check')
    op.drop_constraint('total_nonneg', 'runs', type_='check')
    op.drop_column('runs', 'estimated_micros')
    op.drop_column('runs', 'settled_micros')
    op.drop_column('runs', 'reserved_micros')
    op.drop_column('runs', 'total_budget_micros')


def downgrade() -> None:
    op.add_column('runs', sa.Column('total_budget_micros', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('runs', sa.Column('reserved_micros', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('runs', sa.Column('settled_micros', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('runs', sa.Column('estimated_micros', sa.BigInteger(), nullable=False, server_default='0'))
    op.create_check_constraint('total_nonneg', 'runs', 'total_budget_micros >= 0')
    op.create_check_constraint('reserved_nonneg', 'runs', 'reserved_micros >= 0')
    op.create_check_constraint('settled_nonneg', 'runs', 'settled_micros >= 0')
    op.create_check_constraint('estimated_nonneg', 'runs', 'estimated_micros >= 0')
    op.create_table(
        'budget_entries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('run_id', sa.UUID(), nullable=False),
        sa.Column('attempt_id', sa.UUID(), nullable=True),
        sa.Column('entry_type', sa.Text(), nullable=False),
        sa.Column('amount_micros', sa.BigInteger(), nullable=False),
        sa.Column('idem_key', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name='fk_budget_entries_run_id_runs'),
        sa.PrimaryKeyConstraint('id', name='pk_budget_entries'),
        sa.UniqueConstraint('run_id', 'idem_key', name='uq_budget_run_idem'),
        sa.CheckConstraint('amount_micros >= 0', name='amount_nonneg'),
    )
    op.create_index('ix_budget_run_type', 'budget_entries', ['run_id', 'entry_type'])
