"""s06e panel registrations

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-09-28 23:50:00.000000

Adds the append-only panel_registrations table (campaign-policy §2.1.1):
the frozen, content-addressed evidence bundle for one panel draw —
source raw object, times, the complete ticker -> permanent-id mapping
with evidence-based identity validity, the normalized frame, and the
sample (seed, quotas, selected ids and hashes). Restore recreates the
same permanent ids; the registration gate re-derives and cross-checks
every field.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('panel_registrations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('index', sa.Text(), nullable=False),
    sa.Column('stratification_level', sa.Text(), nullable=False),
    sa.Column('protocol_ref', sa.Text(), nullable=False),
    sa.Column('protocol_sha256', sa.Text(), nullable=False),
    sa.Column('seed', sa.Text(), nullable=False),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('source_raw_object_id', sa.UUID(), nullable=False),
    sa.Column('observed_at', sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('usable_at', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('source_available_basis', sa.Text(), nullable=False),
    sa.Column('frame_as_of', sa.Date(), nullable=False),
    sa.Column('frame_sha256', sa.Text(), nullable=False),
    sa.Column('mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('mapping_sha256', sa.Text(), nullable=False),
    sa.Column('quotas', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('selected', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('selected_list_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("frame_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_panel_registrations_frame_sha_format')),
    sa.CheckConstraint("mapping_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_panel_registrations_mapping_sha_format')),
    sa.CheckConstraint("selected_list_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_panel_registrations_selected_sha_format')),
    sa.ForeignKeyConstraint(['source_raw_object_id'], ['raw_objects.id'], name=op.f('fk_panel_registrations_source_raw_object_id_raw_objects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_panel_registrations')),
    sa.UniqueConstraint('index', 'frame_sha256', 'mapping_sha256', name=op.f('uq_panel_registration'))
    )
    op.execute(
        "CREATE TRIGGER panel_registrations_no_update BEFORE UPDATE OR DELETE ON panel_registrations "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER panel_registrations_no_truncate BEFORE TRUNCATE ON panel_registrations "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS panel_registrations_no_truncate ON panel_registrations;")
    op.execute("DROP TRIGGER IF EXISTS panel_registrations_no_update ON panel_registrations;")
    op.drop_table('panel_registrations')
