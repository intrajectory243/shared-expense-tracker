"""add categories table

Revision ID: c4a8f1e9b3d2
Revises: b3e7291caa04
Create Date: 2026-08-20 14:00:00.000000

Roadmap Phase 9: a household's own editable expense categories
(app/models.py::Category). Schema-only, no backfill -- an existing
household's categories are seeded lazily the first time GET /categories
is called for it (app/routers/categories.py), same as a brand-new
household file's tables only ever get created on first per-household
access to begin with.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a8f1e9b3d2'
down_revision: Union[str, Sequence[str], None] = 'b3e7291caa04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('household_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_categories_household_id'), ['household_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_categories_household_id'))
    op.drop_table('categories')
