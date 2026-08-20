"""initial household schema

Revision ID: 7d3f0c9a1b52
Revises:
Create Date: 2026-08-20 00:00:00.000000

First migration in the per-household stream (roadmap Phase 7). Runs once
per household's own SQLite file, not against the shared one. These four
tables used to live in the shared file's f60859b057c9 migration -- see that
file's docstring, and backend/scripts/split_to_sharded_dbs.py, for how an
existing install's data actually got here.

household_id/payer_id/created_by_id/from_user_id/to_user_id/user_id below
are plain integer columns, not ForeignKeys: the rows they'd point to
(households, users) live in the separate shared file, which SQLite can't
enforce a constraint across. expense_id (expense_participants ->
expenses) stays a real FK since both tables live in this same file.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d3f0c9a1b52'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('balance_cache',
    sa.Column('household_id', sa.Integer(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('household_id')
    )
    op.create_table('expenses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('household_id', sa.Integer(), nullable=False),
    sa.Column('payer_id', sa.Integer(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_expenses_household_id'), ['household_id'], unique=False)

    op.create_table('settlements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('household_id', sa.Integer(), nullable=False),
    sa.Column('from_user_id', sa.Integer(), nullable=False),
    sa.Column('to_user_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_settlements_household_id'), ['household_id'], unique=False)

    op.create_table('expense_participants',
    sa.Column('expense_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('share', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['expense_id'], ['expenses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('expense_id', 'user_id')
    )


def downgrade() -> None:
    op.drop_table('expense_participants')
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_settlements_household_id'))

    op.drop_table('settlements')
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_expenses_household_id'))

    op.drop_table('expenses')
    op.drop_table('balance_cache')
