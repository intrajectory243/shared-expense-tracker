"""add soft-delete to expenses and settlements

Revision ID: e1f2a3b4c5d6
Revises: c4a8f1e9b3d2
Create Date: 2026-09-03 12:00:00.000000

Household admins can delete an expense or settlement; it lingers as
"deleted" for a grace window (settings.trash_retention_days) before an
opportunistic purge removes it for good. `deleted_at` NULL = live row;
non-NULL = in the trash. `deleted_by_id` is a plain user-id string (crosses
into the shared file, same as payer_id -- not a FK). Schema-only, no
backfill: every existing row is live, i.e. deleted_at stays NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'c4a8f1e9b3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('expenses', 'settlements'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column('deleted_by_id', sa.String(length=36), nullable=True))
            batch_op.create_index(batch_op.f(f'ix_{table}_deleted_at'), ['deleted_at'], unique=False)


def downgrade() -> None:
    for table in ('expenses', 'settlements'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f'ix_{table}_deleted_at'))
            batch_op.drop_column('deleted_by_id')
            batch_op.drop_column('deleted_at')
