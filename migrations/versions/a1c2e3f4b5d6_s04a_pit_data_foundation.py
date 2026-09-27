"""s04a pit data foundation

Revision ID: a1c2e3f4b5d6
Revises: 0d8471d9eb62
Create Date: 2026-09-27 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, None] = '0d8471d9eb62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('data_sources',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('vendor_version', sa.Text(), nullable=False),
    sa.Column('license_tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_sources'))
    )
    op.create_table('securities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('asset_class', sa.Text(), server_default='equity', nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("asset_class IN ('equity', 'etf')", name=op.f('ck_securities_asset_class_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_securities'))
    )
    op.create_table('security_identities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('security_id', sa.UUID(), nullable=False),
    sa.Column('identifier_type', sa.Text(), nullable=False),
    sa.Column('identifier', sa.Text(), nullable=False),
    sa.Column('venue', sa.Text(), server_default='US', nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("identifier_type IN ('ticker', 'perma_ticker', 'cusip', 'isin', 'figi')", name=op.f('ck_security_identities_id_type_valid')),
    sa.ForeignKeyConstraint(['security_id'], ['securities.id'], name=op.f('fk_security_identities_security_id_securities')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_identities')),
    sa.UniqueConstraint('identifier_type', 'identifier', 'venue', 'valid_from', name=op.f('uq_security_identities_id_from'))
    )
    op.create_index(op.f('ix_security_identities_identifier_type_identifier_venue'), 'security_identities', ['identifier_type', 'identifier', 'venue'], unique=False)
    op.create_table('raw_objects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('query', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('query_sha256', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('content_type', sa.Text(), server_default='application/json', nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('parser_version', sa.Text(), nullable=False),
    sa.Column('source_available_at', sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('source_available_basis', sa.Text(), nullable=False),
    sa.Column('ingested_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('usable_at', sa.TIMESTAMP(timezone=True), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_raw_objects_content_sha_format')),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_raw_objects_source_id_data_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raw_objects')),
    sa.UniqueConstraint('source_id', 'endpoint', 'query_sha256', 'content_sha256', name=op.f('uq_raw_objects_content'))
    )
    op.create_index(op.f('ix_raw_objects_source_id_endpoint'), 'raw_objects', ['source_id', 'endpoint'], unique=False)
    op.create_table('price_observations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('raw_object_id', sa.UUID(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.UUID(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open', sa.Numeric(20, 8), nullable=False),
    sa.Column('high', sa.Numeric(20, 8), nullable=False),
    sa.Column('low', sa.Numeric(20, 8), nullable=False),
    sa.Column('close', sa.Numeric(20, 8), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('adj_close', sa.Numeric(20, 8), nullable=True),
    sa.Column('div_cash', sa.Numeric(12, 6), nullable=False),
    sa.Column('split_factor', sa.Numeric(10, 4), nullable=False),
    sa.Column('quality', sa.Text(), server_default='ok', nullable=False),
    sa.CheckConstraint("quality IN ('ok', 'zero_volume')", name=op.f('ck_price_observations_quality_valid')),
    sa.ForeignKeyConstraint(['raw_object_id'], ['raw_objects.id'], name=op.f('fk_price_observations_raw_object_id_raw_objects')),
    sa.ForeignKeyConstraint(['security_id'], ['securities.id'], name=op.f('fk_price_observations_security_id_securities')),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_price_observations_source_id_data_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_observations')),
    sa.UniqueConstraint('raw_object_id', 'trade_date', name=op.f('uq_price_observations_raw_date'))
    )
    op.create_index(op.f('ix_price_observations_security_id_trade_date'), 'price_observations', ['security_id', 'trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_price_observations_security_id_trade_date'), table_name='price_observations')
    op.drop_table('price_observations')
    op.drop_index(op.f('ix_raw_objects_source_id_endpoint'), table_name='raw_objects')
    op.drop_table('raw_objects')
    op.drop_index(op.f('ix_security_identities_identifier_type_identifier_venue'), table_name='security_identities')
    op.drop_table('security_identities')
    op.drop_table('securities')
    op.drop_table('data_sources')
