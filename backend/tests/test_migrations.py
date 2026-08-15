"""Guards the property that actually matters for self-hosters: a fresh
install (alembic upgrade head) must end up with exactly the schema the
ORM models describe -- no drift between alembic/versions/*.py and
app/models.py. If a future migration is hand-edited wrong, or a model
change ships without a matching migration, this is what catches it."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401 -- populates Base.metadata
from app.config import settings
from app.database import Base
from app.migrations import run_migrations


def _fresh_sqlite_url():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # alembic/create_all must build it from nothing
    return f"sqlite:///{path}", path


@pytest.fixture()
def two_fresh_databases(monkeypatch):
    migrated_url, migrated_path = _fresh_sqlite_url()
    direct_url, direct_path = _fresh_sqlite_url()
    monkeypatch.setattr(settings, "database_url", migrated_url)
    try:
        yield migrated_url, direct_url
    finally:
        for path in (migrated_path, direct_path):
            if os.path.exists(path):
                os.remove(path)


def test_migrated_schema_matches_the_orm_models_exactly(two_fresh_databases):
    migrated_url, direct_url = two_fresh_databases

    run_migrations()  # path A: alembic upgrade head
    direct_engine = create_engine(direct_url)
    Base.metadata.create_all(bind=direct_engine)  # path B: straight from the models

    migrated_inspector = inspect(create_engine(migrated_url))
    direct_inspector = inspect(direct_engine)

    migrated_tables = set(migrated_inspector.get_table_names()) - {"alembic_version"}
    direct_tables = set(direct_inspector.get_table_names())
    assert migrated_tables == direct_tables

    for table in sorted(direct_tables):
        migrated_cols = {(c["name"], str(c["type"]), c["nullable"]) for c in migrated_inspector.get_columns(table)}
        direct_cols = {(c["name"], str(c["type"]), c["nullable"]) for c in direct_inspector.get_columns(table)}
        assert migrated_cols == direct_cols, f"schema drift in '{table}'"

        migrated_pk = migrated_inspector.get_pk_constraint(table)["constrained_columns"]
        direct_pk = direct_inspector.get_pk_constraint(table)["constrained_columns"]
        assert set(migrated_pk) == set(direct_pk), f"primary key drift in '{table}'"


def test_run_migrations_is_idempotent(two_fresh_databases):
    """Runs on every app startup, including restarts of an already-migrated
    instance -- a second call against a DB already at head must be a no-op,
    not an error."""
    run_migrations()
    run_migrations()  # must not raise
