"""s03a sandbox runner and artifacts

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-27 20:30:00.000000

Adds the artifacts table: validated outputs of untrusted sandbox
code, bound to the fenced attempt that produced them. Append-only
(joins the trigger set). Content is inline text in MVP; the content
hash survives a later move to object storage.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('artifacts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('extension', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_artifacts_content_sha_format')),
    sa.CheckConstraint("size_bytes >= 0", name=op.f('ck_artifacts_size_nonneg')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_artifacts_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_artifacts')),
    sa.UniqueConstraint('job_id', 'attempt_no', 'path', name=op.f('uq_artifacts_job_attempt_path'))
    )
    op.create_index(op.f('ix_artifacts_job'), 'artifacts', ['job_id'], unique=False)
    op.create_index(op.f('ix_artifacts_tenant'), 'artifacts', ['tenant_id'], unique=False)

    op.execute(
        "CREATE TRIGGER artifacts_no_update BEFORE UPDATE OR DELETE ON artifacts "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER artifacts_no_truncate BEFORE TRUNCATE ON artifacts "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS artifacts_no_truncate ON artifacts;")
    op.execute("DROP TRIGGER IF EXISTS artifacts_no_update ON artifacts;")
    op.drop_index(op.f('ix_artifacts_tenant'), table_name='artifacts')
    op.drop_index(op.f('ix_artifacts_job'), table_name='artifacts')
    op.drop_table('artifacts')
