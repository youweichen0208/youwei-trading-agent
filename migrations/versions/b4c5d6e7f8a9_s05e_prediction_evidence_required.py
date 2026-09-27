"""s05e produced predictions require frozen evidence

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-28 22:00:00.000000

Adds the produced_evidence_required CHECK to predictions: a produced
quant_model or llm_adjusted position must reference the frozen evidence
snapshot it was computed from (S04 acceptance: every model input traces
to data frozen at or before the cutoff; S05 item: produced sources
force the evidence reference). The sealing path enforces the same rule
plus point-in-time discipline (forward mode, as_of <= case cutoff) at
the ledger boundary.

Preflight for databases with existing data (must return 0 before
migrating; violating rows need backfill or explicit disposition):

    SELECT count(*) FROM predictions
    WHERE source IN ('quant_model', 'llm_adjusted')
      AND source_status = 'produced'
      AND evidence_snapshot_id IS NULL;
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f('ck_predictions_produced_evidence_required'),
        'predictions',
        "source NOT IN ('quant_model', 'llm_adjusted') OR source_status <> 'produced' "
        "OR evidence_snapshot_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('ck_predictions_produced_evidence_required'), 'predictions'
    )
