"""Guards the property that actually matters for self-hosters: a fresh
install (alembic upgrade head) must end up with exactly the schema the
ORM models describe -- no drift between alembic/*/versions/*.py and
app/models.py, for BOTH migration streams (roadmap Phase 7 split the
single stream into a shared one and a per-household one -- see
app/database.py's module docstring). If a future migration is hand-edited
wrong, or a model change ships without a matching migration, this is what
catches it."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401 -- populates SharedBase/HouseholdBase.metadata
from app.config import settings
from app.database import HouseholdBase, SharedBase
from app.household_db import run_household_migrations
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


@pytest.fixture()
def two_fresh_household_paths():
    from pathlib import Path

    _url_a, path_a = _fresh_sqlite_url()
    _url_b, path_b = _fresh_sqlite_url()
    try:
        yield Path(path_a), Path(path_b)
    finally:
        for path in (path_a, path_b):
            if os.path.exists(path):
                os.remove(path)


def _assert_schemas_match(migrated_inspector, direct_inspector):
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


def test_migrated_schema_matches_the_orm_models_exactly(two_fresh_databases):
    migrated_url, direct_url = two_fresh_databases

    run_migrations()  # path A: alembic upgrade head (both streams; household is a no-op here, nothing to walk)
    direct_engine = create_engine(direct_url)
    SharedBase.metadata.create_all(bind=direct_engine)  # path B: straight from the models

    _assert_schemas_match(inspect(create_engine(migrated_url)), inspect(direct_engine))


def test_run_migrations_is_idempotent(two_fresh_databases):
    """Runs on every app startup, including restarts of an already-migrated
    instance -- a second call against a DB already at head must be a no-op,
    not an error."""
    run_migrations()
    run_migrations()  # must not raise


def test_migrated_household_schema_matches_the_orm_models_exactly(two_fresh_household_paths):
    migrated_path, direct_path = two_fresh_household_paths

    run_household_migrations(migrated_path)  # path A: alembic upgrade head
    direct_engine = create_engine(f"sqlite:///{direct_path}")
    HouseholdBase.metadata.create_all(bind=direct_engine)  # path B: straight from the models

    _assert_schemas_match(inspect(create_engine(f"sqlite:///{migrated_path}")), inspect(direct_engine))


def test_run_household_migrations_is_idempotent(two_fresh_household_paths):
    migrated_path, _direct_path = two_fresh_household_paths
    run_household_migrations(migrated_path)
    run_household_migrations(migrated_path)  # must not raise
