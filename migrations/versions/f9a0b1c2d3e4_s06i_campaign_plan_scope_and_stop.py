"""S06i: campaign plan range, structured approval scope, and stop control

- campaigns gains the frozen weekly cutoff list and its hash plus the full
  experiment plan hash (planned_cutoffs, planned_cutoffs_sha256,
  campaign_plan_sha256). Existing rows stay NULL; new registrations must fill
  them.
- release_approvals gains a structured scope_manifest + scope_sha256. The
  uniqueness key moves from (release, approver) to (release, approver,
  scope_sha256) so one approver may approve the same release for different
  plans; re-submitting the same full approval is idempotent. The legacy
  free-text scope column stays for audit and grants no new authorization.
- campaign_control_events: append-only control facts (stop_new_batches) with
  reason/actor/time; one per (campaign, event_type). It never deletes a plan
  or case; outcome follow-up is independent of this switch.

Campaigns already sit in the append-only trigger set, so adding columns is
fine (ALTER ADD COLUMN is not UPDATE/DELETE); the new control table joins the
trigger set too.
"""

from alembic import op
import sqlalchemy as sa

revision = 'f9a0b1c2d3e4'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('campaigns', sa.Column('planned_cutoffs', sa.JSON(), nullable=True))
    op.add_column('campaigns', sa.Column('planned_cutoffs_sha256', sa.Text(), nullable=True))
    op.add_column('campaigns', sa.Column('campaign_plan_sha256', sa.Text(), nullable=True))

    op.add_column('release_approvals', sa.Column('scope_manifest', sa.JSON(), nullable=True))
    op.add_column('release_approvals', sa.Column('scope_sha256', sa.Text(), nullable=True))

    op.drop_constraint('uq_approvals_release_approver', 'release_approvals', type_='unique')
    op.create_unique_constraint(
        'uq_approvals_release_approver_scope',
        'release_approvals',
        ['release_row_id', 'approver_principal_id', 'scope_sha256'],
    )

    op.create_table(
        'campaign_control_events',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('campaign_id', sa.UUID(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('actor_principal_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('campaign_id', 'event_type', name='uq_control_campaign_event'),
    )
    op.execute(
        "CREATE TRIGGER campaign_control_events_no_update "
        "BEFORE UPDATE OR DELETE ON campaign_control_events "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER campaign_control_events_no_truncate "
        "BEFORE TRUNCATE ON campaign_control_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS campaign_control_events_no_truncate ON campaign_control_events")
    op.execute("DROP TRIGGER IF EXISTS campaign_control_events_no_update ON campaign_control_events")
    op.drop_table('campaign_control_events')

    op.drop_constraint('uq_approvals_release_approver_scope', 'release_approvals', type_='unique')
    op.create_unique_constraint(
        'uq_approvals_release_approver',
        'release_approvals',
        ['release_row_id', 'approver_principal_id'],
    )

    op.drop_column('release_approvals', 'scope_sha256')
    op.drop_column('release_approvals', 'scope_manifest')
    op.drop_column('campaigns', 'campaign_plan_sha256')
    op.drop_column('campaigns', 'planned_cutoffs_sha256')
    op.drop_column('campaigns', 'planned_cutoffs')
