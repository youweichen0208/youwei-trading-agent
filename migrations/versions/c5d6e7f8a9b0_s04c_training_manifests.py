"""s04c training manifests

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-28 23:30:00.000000

Adds the append-only training_manifests table (S04, campaign-policy
§5): registered manifests fix the feature sets, per-model
preprocessing, label maturation rules, fitting window, calibration
and content-hashed model artifacts that a ResearchRelease references
(training_manifest_ref + sha256, verified at campaign registration).
The table joins the append-only trigger set like research_releases.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('training_manifests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('manifest_id', sa.Text(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_training_manifests_content_sha_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_training_manifests')),
    sa.UniqueConstraint('manifest_id', name=op.f('uq_training_manifests_manifest_id'))
    )
    op.execute(
        "CREATE TRIGGER training_manifests_no_update BEFORE UPDATE OR DELETE ON training_manifests "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER training_manifests_no_truncate BEFORE TRUNCATE ON training_manifests "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS training_manifests_no_truncate ON training_manifests;")
    op.execute("DROP TRIGGER IF EXISTS training_manifests_no_update ON training_manifests;")
    op.drop_table('training_manifests')
