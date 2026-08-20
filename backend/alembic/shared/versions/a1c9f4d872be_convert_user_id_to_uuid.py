"""convert user id to uuid

Revision ID: a1c9f4d872be
Revises: 42dec7773c6b
Create Date: 2026-08-20 12:00:00.000000

Roadmap Phase 8 prep: User.id becomes a deterministic uuid5(email) string
(app/identity.py) instead of an autoincrement integer. A restored
per-household file needs a user reference that means the same thing on
any machine -- an autoincrement int on a fresh instance could collide
with an unrelated real account there, silently misattributing an expense.

Existing rows are converted in place, keyed by email, so every row's data
survives. The old-id -> new-id mapping this needs is also the thing
backend/scripts/migrate_user_ids_to_uuid.py needs afterward, to fix up
the matching payer_id/created_by_id/user_id/from_user_id/to_user_id
columns in every per-household file -- this migration can't reach those
itself (they live in separate SQLite files, see app/household_db.py), so
it writes the mapping out as a JSON sidecar next to the shared DB file
for that script to pick up. Only written if there was anything to
convert; a genuinely fresh install has no users yet, so this whole
migration is a no-op for it and no sidecar file appears.
"""
import json
import uuid
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c9f4d872be'
down_revision: Union[str, Sequence[str], None] = '42dec7773c6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match app/identity.py::APP_NAMESPACE exactly -- this migration can't
# safely import the app package (alembic env.py runs it standalone), and a
# mismatched namespace would compute different ids than the running app.
_APP_NAMESPACE = uuid.UUID("1f2f6a3e-6b0a-4b8a-9b0a-2f6a3e6b0a4b")


def upgrade() -> None:
    conn = op.get_bind()

    users = conn.execute(sa.text(
        "SELECT id, email, password_hash, name, role, status, created_at, "
        "invite_token, household_id, language FROM users"
    )).fetchall()
    # Schema is rebuilt as String(36) either way, even with zero existing
    # rows (a fresh install), so it stays in lockstep with the ORM models --
    # only the data-conversion and sidecar-mapping steps below are
    # meaningful skips when there's nothing to convert.
    id_map = {row.id: str(uuid.uuid5(_APP_NAMESPACE, row.email.strip().lower())) for row in users}

    op.create_table(
        'users_new',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('role', sa.Enum('admin', 'member', name='userrole'), nullable=False),
        sa.Column('status', sa.Enum('pending', 'approved', 'moved_out', 'removed', name='userstatus'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('invite_token', sa.String(length=64), nullable=True),
        sa.Column('household_id', sa.Integer(), nullable=True),
        sa.Column('language', sa.Enum('en', 'fa', name='language'), server_default='en', nullable=False),
        sa.ForeignKeyConstraint(['household_id'], ['households.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invite_token'),
    )
    for row in users:
        conn.execute(
            sa.text(
                "INSERT INTO users_new (id, email, password_hash, name, role, status, created_at, "
                "invite_token, household_id, language) "
                "VALUES (:id, :email, :password_hash, :name, :role, :status, :created_at, "
                ":invite_token, :household_id, :language)"
            ),
            {
                "id": id_map[row.id],
                "email": row.email,
                "password_hash": row.password_hash,
                "name": row.name,
                "role": row.role,
                "status": row.status,
                "created_at": row.created_at,
                "invite_token": row.invite_token,
                "household_id": row.household_id,
                "language": row.language,
            },
        )

    push_rows = conn.execute(sa.text(
        "SELECT id, user_id, endpoint, p256dh, auth, created_at FROM push_subscriptions"
    )).fetchall()

    op.drop_table('push_subscriptions')  # first: its FK points at the old integer users.id
    op.drop_table('users')
    op.rename_table('users_new', 'users')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('endpoint', sa.String(length=500), nullable=False),
        sa.Column('p256dh', sa.String(length=255), nullable=False),
        sa.Column('auth', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint'),
    )
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_push_subscriptions_user_id'), ['user_id'], unique=False)

    for row in push_rows:
        new_user_id = id_map.get(row.user_id)
        if new_user_id is None:
            continue  # orphaned subscription pointing at a since-deleted user; drop it silently
        conn.execute(
            sa.text(
                "INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth, created_at) "
                "VALUES (:id, :user_id, :endpoint, :p256dh, :auth, :created_at)"
            ),
            {
                "id": row.id,
                "user_id": new_user_id,
                "endpoint": row.endpoint,
                "p256dh": row.p256dh,
                "auth": row.auth,
                "created_at": row.created_at,
            },
        )

    if id_map:
        db_path = Path(conn.engine.url.database)
        map_path = db_path.with_suffix(db_path.suffix + ".user-id-migration-map.json")
        map_path.write_text(json.dumps(id_map))


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrading would need to invent a new autoincrement id per user; not supported."
    )
