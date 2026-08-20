"""convert user id references to uuid

Revision ID: b3e7291caa04
Revises: 7d3f0c9a1b52
Create Date: 2026-08-20 12:00:00.000000

Companion to the shared stream's a1c9f4d872be migration (see its
docstring) -- schema-only here. payer_id/created_by_id/user_id/
from_user_id/to_user_id become VARCHAR(36), matching the shared file's
users.id. The *values* in these columns aren't touched by this migration:
they still hold the old integer ids as text until
backend/scripts/migrate_user_ids_to_uuid.py is run by hand. It has to be
schema-only -- this migration runs automatically on every app startup
(app/migrations.py), before anyone's had a chance to run that script, so
it can never safely assume the shared-file mapping it would need is
available yet. A genuinely fresh household file has no existing rows, so
this is effectively a no-op for it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e7291caa04'
down_revision: Union[str, Sequence[str], None] = '7d3f0c9a1b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.alter_column('payer_id', existing_type=sa.Integer(), type_=sa.String(length=36))
        batch_op.alter_column('created_by_id', existing_type=sa.Integer(), type_=sa.String(length=36))

    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.alter_column('from_user_id', existing_type=sa.Integer(), type_=sa.String(length=36))
        batch_op.alter_column('to_user_id', existing_type=sa.Integer(), type_=sa.String(length=36))

    with op.batch_alter_table('expense_participants', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), type_=sa.String(length=36))


def downgrade() -> None:
    with op.batch_alter_table('expense_participants', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.String(length=36), type_=sa.Integer())

    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.alter_column('to_user_id', existing_type=sa.String(length=36), type_=sa.Integer())
        batch_op.alter_column('from_user_id', existing_type=sa.String(length=36), type_=sa.Integer())

    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), type_=sa.Integer())
        batch_op.alter_column('payer_id', existing_type=sa.String(length=36), type_=sa.Integer())
