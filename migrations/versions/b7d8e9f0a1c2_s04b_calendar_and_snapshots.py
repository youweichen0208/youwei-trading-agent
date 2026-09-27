"""s04b calendar and snapshots

Revision ID: b7d8e9f0a1c2
Revises: a1c2e3f4b5d6
Create Date: 2026-09-27 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b7d8e9f0a1c2'
down_revision: Union[str, None] = 'a1c2e3f4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('calendar_builds',
    sa.Column('version', sa.Text(), nullable=False),
    sa.Column('venue', sa.Text(), nullable=False),
    sa.Column('year_start', sa.Integer(), nullable=False),
    sa.Column('year_end', sa.Integer(), nullable=False),
    sa.Column('rules_version', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('day_count', sa.Integer(), nullable=False),
    sa.Column('special_closures', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('generated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_calendar_builds_content_sha_format')),
    sa.PrimaryKeyConstraint('version', name=op.f('pk_calendar_builds'))
    )
    op.create_table('calendar_days',
    sa.Column('venue', sa.Text(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('is_trading', sa.Boolean(), nullable=False),
    sa.Column('early_close', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('build_version', sa.Text(), nullable=False),
    sa.CheckConstraint("is_trading OR NOT early_close", name=op.f('ck_calendar_days_early_implies_trading')),
    sa.ForeignKeyConstraint(['build_version'], ['calendar_builds.version'], name=op.f('fk_calendar_days_build_version_calendar_builds')),
    sa.PrimaryKeyConstraint('venue', 'date', name=op.f('pk_calendar_days')),
    )
    op.create_index(op.f('ix_calendar_days_venue_date_is_trading'), 'calendar_days', ['venue', 'date', 'is_trading'], unique=False)
    op.create_table('snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('query', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('query_sha256', sa.Text(), nullable=False),
    sa.Column('as_of', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('mode', sa.Text(), nullable=False),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_attempt', sa.UUID(), nullable=True),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_snapshots_content_sha_format')),
    sa.CheckConstraint("mode IN ('forward', 'historical_source')", name=op.f('ck_snapshots_mode_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_snapshots')),
    sa.UniqueConstraint('kind', 'query_sha256', 'content_sha256', name=op.f('uq_snapshots_query_content'))
    )
    op.create_index(op.f('ix_snapshots_kind_created_at'), 'snapshots', ['kind', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_snapshots_kind_created_at'), table_name='snapshots')
    op.drop_table('snapshots')
    op.drop_index(op.f('ix_calendar_days_venue_date_is_trading'), table_name='calendar_days')
    op.drop_table('calendar_days')
    op.drop_table('calendar_builds')
