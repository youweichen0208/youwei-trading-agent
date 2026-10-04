"""release code exceptions (bounded compatibility registry)

Revision ID: c2d3e4f5a6b7
Revises: b2c3d4e5f6a7
Create Date: 2026-10-03

Adds ``release_code_exceptions``: the append-only registry for owner-accepted
differences between an approved release's registered code_files and the code
actually deployed in production (owner decision 2026-10-03, review of the
S12 D2 enablement prep).

One row per (release, deployed image digest): the ACTUAL code_files hashes of
the deployed build, the diff summary, the verification evidence (tests,
commands, document reference + hash), the approver and the decision basis.
``created_at`` is the server clock at registration time — approval times are
never backdated (a difference discovered after deployment is recorded as
such, in ``decision_basis``).

Scope is explicit per row (campaign + image digest): future deployments do
NOT inherit an exception — a further code change requires a new release or a
new, separately accepted exception row.

UPDATE/DELETE/TRUNCATE blocked via youwei_ledger_block_mutation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('release_code_exceptions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('deployed_image_digest', sa.Text(), nullable=False),
    sa.Column('code_files_actual', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('diff_summary', sa.Text(), nullable=False),
    sa.Column('verification', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('approver', sa.Text(), nullable=False),
    sa.Column('decision_basis', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("deployed_image_digest ~ '^sha256:[0-9a-f]{64}$'", name=op.f('ck_rce_digest_format')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_release_code_exceptions_campaign_id_campaigns')),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_release_code_exceptions_release_row_id_research_releases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_release_code_exceptions')),
    sa.UniqueConstraint('release_row_id', 'deployed_image_digest', name=op.f('uq_rce_release_digest')),
    )
    op.execute(
        "CREATE TRIGGER release_code_exceptions_no_update BEFORE UPDATE OR DELETE ON release_code_exceptions "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER release_code_exceptions_no_truncate BEFORE TRUNCATE ON release_code_exceptions "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS release_code_exceptions_no_truncate ON release_code_exceptions")
    op.execute("DROP TRIGGER IF EXISTS release_code_exceptions_no_update ON release_code_exceptions")
    op.drop_table('release_code_exceptions')
