"""s05a campaign registration and forecast ledger

Revision ID: c9d0e1f2a3b4
Revises: b7d8e9f0a1c2
Create Date: 2026-09-27 16:00:00.000000

Adds the S05 planning and sealing layer: research releases with human
approvals, pre-registered campaigns, weekly batches with sealed case
windows, and the append-only forecast ledger (commits, predictions,
commit events) with a per-campaign hash chain.

Ledger immutability (architecture section 6): BEFORE UPDATE/DELETE/
TRUNCATE triggers on the ledger records raise unless the session sets
youwei.ledger_mutation='on' (ops/test escape hatch). ledger_chains is
mutable by design (lock state, not a record).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b7d8e9f0a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Append-only ledger records (triggers block UPDATE/DELETE/TRUNCATE).
LEDGER_TABLES = (
    'research_releases',
    'release_approvals',
    'campaigns',
    'forecast_batches',
    'forecast_cases',
    'forecast_commits',
    'predictions',
    'forecast_commit_events',
)


def upgrade() -> None:
    op.create_table('research_releases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('release_id', sa.Text(), nullable=False),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('release_content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("release_content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_research_releases_content_sha_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_releases')),
    sa.UniqueConstraint('release_id', name=op.f('uq_research_releases_release_id'))
    )
    op.create_table('release_approvals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('approver_principal_id', sa.Text(), nullable=False),
    sa.Column('approved_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('release_content_sha256', sa.Text(), nullable=False),
    sa.Column('scope', sa.Text(), nullable=False),
    sa.Column('basis', sa.Text(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_release_approvals_release_row_id_research_releases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_release_approvals')),
    sa.UniqueConstraint('release_row_id', 'approver_principal_id', name=op.f('uq_approvals_release_approver'))
    )
    op.create_table('campaigns',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('campaign_key', sa.Text(), nullable=False),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('target_specs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('time_protocol_ref', sa.Text(), nullable=False),
    sa.Column('time_protocol_sha256', sa.Text(), nullable=False),
    sa.Column('benchmark_security_id', sa.UUID(), nullable=False),
    sa.Column('panel_security_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('panel_manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled_sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('fallback_policy', sa.Text(), nullable=False),
    sa.Column('primary_metric', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='active', nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("time_protocol_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_campaigns_time_sha_format')),
    sa.ForeignKeyConstraint(['benchmark_security_id'], ['securities.id'], name=op.f('fk_campaigns_benchmark_security_id_securities')),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_campaigns_release_row_id_research_releases')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_campaigns_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_campaigns')),
    sa.UniqueConstraint('tenant_id', 'campaign_key', name=op.f('uq_campaigns_tenant_key'))
    )
    op.create_table('forecast_batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('decision_cutoff_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('prediction_deadline_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('entry_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('batch_manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('backfilled_plan', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_forecast_batches_campaign_id_campaigns')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecast_batches')),
    sa.UniqueConstraint('campaign_id', 'decision_cutoff_utc', name=op.f('uq_batches_campaign_cutoff'))
    )
    op.create_index(op.f('ix_batches_campaign_decision_cutoff_utc'), 'forecast_batches', ['campaign_id', 'decision_cutoff_utc'], unique=False)
    op.create_table('forecast_cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('security_id', sa.UUID(), nullable=False),
    sa.Column('benchmark_security_id', sa.UUID(), nullable=False),
    sa.Column('horizon_td', sa.Integer(), nullable=False),
    sa.Column('target_spec_id', sa.Text(), nullable=False),
    sa.Column('target_spec_sha256', sa.Text(), nullable=False),
    sa.Column('decision_cutoff_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('prediction_deadline_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('entry_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('exit_at_utc', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("horizon_td IN (1, 20, 60)", name=op.f('ck_forecast_cases_horizon_valid')),
    sa.CheckConstraint("target_spec_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_forecast_cases_spec_sha_format')),
    sa.ForeignKeyConstraint(['batch_id'], ['forecast_batches.id'], name=op.f('fk_forecast_cases_batch_id_forecast_batches')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_forecast_cases_campaign_id_campaigns')),
    sa.ForeignKeyConstraint(['security_id'], ['securities.id'], name=op.f('fk_forecast_cases_security_id_securities')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecast_cases')),
    sa.UniqueConstraint('batch_id', 'security_id', 'horizon_td', name=op.f('uq_cases_batch_sec_horizon'))
    )
    op.create_index(op.f('ix_cases_campaign'), 'forecast_cases', ['campaign_id'], unique=False)
    op.create_table('ledger_chains',
    sa.Column('chain_id', sa.Text(), nullable=False),
    sa.Column('head_seq', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('head_hash', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('chain_id', name=op.f('pk_ledger_chains'))
    )
    op.create_table('forecast_commits',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('release_row_id', sa.UUID(), nullable=False),
    sa.Column('chain_id', sa.Text(), nullable=False),
    sa.Column('chain_seq', sa.BigInteger(), nullable=False),
    sa.Column('prev_hash', sa.Text(), nullable=False),
    sa.Column('payload_sha256', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('sealed_at', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=True),
    sa.Column('attempt_no', sa.Integer(), nullable=True),
    sa.Column('input_manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_forecast_commits_content_sha_format')),
    sa.CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_forecast_commits_payload_sha_format')),
    sa.ForeignKeyConstraint(['case_id'], ['forecast_cases.id'], name=op.f('fk_forecast_commits_case_id_forecast_cases')),
    sa.ForeignKeyConstraint(['release_row_id'], ['research_releases.id'], name=op.f('fk_forecast_commits_release_row_id_research_releases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecast_commits')),
    sa.UniqueConstraint('case_id', 'release_row_id', name=op.f('uq_commits_case_release')),
    sa.UniqueConstraint('chain_id', 'chain_seq', name=op.f('uq_commits_chain_seq'))
    )
    op.create_index(op.f('ix_commits_case'), 'forecast_commits', ['case_id'], unique=False)
    op.create_table('predictions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('commit_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_status', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('p_outperform', sa.Numeric(11, 10), nullable=True),
    sa.Column('expected_excess_return', sa.Numeric(20, 10), nullable=True),
    sa.Column('evidence_snapshot_id', sa.UUID(), nullable=True),
    sa.Column('model_version', sa.Text(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('baseline', 'quant_model', 'llm_adjusted')", name=op.f('ck_predictions_source_valid')),
    sa.CheckConstraint("source_status IN ('produced', 'fallback', 'unavailable')", name=op.f('ck_predictions_status_valid')),
    sa.CheckConstraint("p_outperform IS NULL OR (p_outperform >= 0 AND p_outperform <= 1)", name=op.f('ck_predictions_p_range')),
    sa.CheckConstraint("source_status = 'unavailable' OR p_outperform IS NOT NULL", name=op.f('ck_predictions_produced_has_p')),
    sa.CheckConstraint("source_status <> 'unavailable' OR (p_outperform IS NULL AND expected_excess_return IS NULL)", name=op.f('ck_predictions_unavailable_has_no_values')),
    sa.ForeignKeyConstraint(['commit_id'], ['forecast_commits.id'], name=op.f('fk_predictions_commit_id_forecast_commits')),
    sa.ForeignKeyConstraint(['evidence_snapshot_id'], ['snapshots.id'], name=op.f('fk_predictions_evidence_snapshot_id_snapshots')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_predictions')),
    sa.UniqueConstraint('commit_id', 'source', name=op.f('uq_predictions_commit_source'))
    )
    op.create_table('forecast_commit_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('commit_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.Text(), nullable=False),
    sa.Column('occurred_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['commit_id'], ['forecast_commits.id'], name=op.f('fk_forecast_commit_events_commit_id_forecast_commits')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecast_commit_events'))
    )
    op.create_index(op.f('ix_commit_events_commit'), 'forecast_commit_events', ['commit_id', 'occurred_at'], unique=False)
    op.create_index(
        'uq_commit_events_confirmation',
        'forecast_commit_events',
        ['commit_id'],
        unique=True,
        postgresql_where=sa.text("event_type = 'durable_confirmation'"),
    )

    # --- append-only enforcement ---------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION youwei_ledger_block_mutation() RETURNS trigger AS $$
        BEGIN
            IF coalesce(current_setting('youwei.ledger_mutation', true), '') <> 'on' THEN
                RAISE EXCEPTION 'table % is append-only (ledger immutability)', TG_TABLE_NAME;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in LEDGER_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION youwei_ledger_block_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION youwei_ledger_block_mutation();"
        )


def downgrade() -> None:
    for table in LEDGER_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};")
    op.execute("DROP FUNCTION IF EXISTS youwei_ledger_block_mutation();")
    op.drop_index('uq_commit_events_confirmation', table_name='forecast_commit_events')
    op.drop_index(op.f('ix_commit_events_commit'), table_name='forecast_commit_events')
    op.drop_table('forecast_commit_events')
    op.drop_table('predictions')
    op.drop_index(op.f('ix_commits_case'), table_name='forecast_commits')
    op.drop_table('forecast_commits')
    op.drop_table('ledger_chains')
    op.drop_index(op.f('ix_cases_campaign'), table_name='forecast_cases')
    op.drop_table('forecast_cases')
    op.drop_index(op.f('ix_batches_campaign_decision_cutoff_utc'), table_name='forecast_batches')
    op.drop_table('forecast_batches')
    op.drop_table('campaigns')
    op.drop_table('release_approvals')
    op.drop_table('research_releases')
