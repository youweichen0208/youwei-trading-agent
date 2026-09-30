"""s07k estimated cost ledger column

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-09-29 00:00:00.000000

Adds runs.estimated_micros so a settlement priced from placeholder
(unreconciled) rates can be booked as an *estimate* without polluting
settled_micros (which stays reserved for confirmed actuals). The two are
kept separate: settled_micros is reconciled actual cost; estimated_micros
is a placeholder-rate estimate awaiting reconciliation (via a later
``adjust`` entry).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'runs',
        sa.Column(
            'estimated_micros',
            sa.BigInteger(),
            nullable=False,
            server_default='0',
        ),
    )
    op.create_check_constraint(
        'estimated_nonneg', 'runs', 'estimated_micros >= 0'
    )


def downgrade() -> None:
    op.drop_constraint('estimated_nonneg', 'runs', type_='check')
    op.drop_column('runs', 'estimated_micros')
