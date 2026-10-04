"""s08c experiment records

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
Create Date: 2026-10-03 10:00:00.000000

Adds the S08 controlled-exploration Core tables (D2 minimal requirements
1/4/5 of docs/research/s08-exploration-loop-design.md):

- ``experiment_records``: the Controller's pre-dispatch registration of one
  experiment — bindings (tenant/run/job/attempt/case), the parent research
  turn's evidence hash, the exec config version, the research instance's
  ask (question/motivation/requested_shape), the limits the Runner
  enforces, and the frozen snapshot the Runner injects. One row per
  experiment_invocation_id, INSERT-only.
- ``experiment_outcomes``: the accepted outcome — the experiment result
  verified against the Runner's receipts at the write boundary (fencing
  re-checked). One row per experiment, INSERT-only.

Both carry the ledger append-only triggers (youwei_ledger_block_mutation).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('experiment_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('evidence_sha256', sa.Text(), nullable=False),
    sa.Column('exec_config_version', sa.Text(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('motivation', sa.Text(), nullable=False),
    sa.Column('requested_shape', sa.Text(), nullable=False),
    sa.Column('limits', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('registered_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_experiment_records_evidence_sha_format')),
    sa.CheckConstraint("attempt_no > 0", name=op.f('ck_experiment_records_attempt_no_positive')),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], name=op.f('fk_experiment_records_attempt_id_attempts')),
    sa.ForeignKeyConstraint(['case_id'], ['forecast_cases.id'], name=op.f('fk_experiment_records_case_id_forecast_cases')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_experiment_records_job_id_jobs')),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_experiment_records_run_id_runs')),
    sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], name=op.f('fk_experiment_records_snapshot_id_snapshots')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_experiment_records_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_experiment_records')),
    )
    op.create_index(op.f('ix_experiment_records_case'), 'experiment_records', ['case_id'], unique=False)

    op.create_table('experiment_outcomes',
    sa.Column('experiment_invocation_id', sa.UUID(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('receipts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('image', sa.Text(), nullable=False),
    sa.Column('snapshot_sha256', sa.Text(), nullable=False),
    sa.Column('accepted_attempt_id', sa.UUID(), nullable=False),
    sa.Column('accepted_attempt_no', sa.Integer(), nullable=False),
    sa.Column('accepted_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_experiment_outcomes_snapshot_sha_format')),
    sa.CheckConstraint("accepted_attempt_no > 0", name=op.f('ck_experiment_outcomes_attempt_no_positive')),
    sa.ForeignKeyConstraint(['accepted_attempt_id'], ['attempts.id'], name=op.f('fk_experiment_outcomes_accepted_attempt_id_attempts')),
    sa.ForeignKeyConstraint(['experiment_invocation_id'], ['experiment_records.id'], name=op.f('fk_experiment_outcomes_experiment_invocation_id_experiment_records')),
    sa.PrimaryKeyConstraint('experiment_invocation_id', name=op.f('pk_experiment_outcomes')),
    )

    op.execute(
        "CREATE TRIGGER experiment_records_no_update BEFORE UPDATE OR DELETE ON experiment_records "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER experiment_records_no_truncate BEFORE TRUNCATE ON experiment_records "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER experiment_outcomes_no_update BEFORE UPDATE OR DELETE ON experiment_outcomes "
        "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )
    op.execute(
        "CREATE TRIGGER experiment_outcomes_no_truncate BEFORE TRUNCATE ON experiment_outcomes "
        "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS experiment_outcomes_no_truncate ON experiment_outcomes;")
    op.execute("DROP TRIGGER IF EXISTS experiment_outcomes_no_update ON experiment_outcomes;")
    op.execute("DROP TRIGGER IF EXISTS experiment_records_no_truncate ON experiment_records;")
    op.execute("DROP TRIGGER IF EXISTS experiment_records_no_update ON experiment_records;")
    op.drop_table('experiment_outcomes')
    op.drop_index(op.f('ix_experiment_records_case'), table_name='experiment_records')
    op.drop_table('experiment_records')
