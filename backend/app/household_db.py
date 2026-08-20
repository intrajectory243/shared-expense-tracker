"""Per-household SQLite database registry (roadmap Phase 7). Expense/
ExpenseParticipant/Settlement/BalanceCache (app/database.py's HouseholdBase)
live in one file per household_id instead of the shared file -- see
app/database.py's module docstring for the full rationale.

A household's file is created lazily, the first time anything asks for its
session (could be the first expense, or just the first balance check on an
empty household) -- never at signup/household-creation time, since nothing
per-household has any rows to create before then.

Engines are kept in an LRU-capped registry so a long-running process doesn't
accumulate one open SQLite connection pool per household forever. Evicting
one is safe even under load: Engine.dispose() closes idle pooled
connections but doesn't forcibly sever a connection an in-flight request
already checked out -- that connection finishes normally and is discarded
on return, per SQLAlchemy's own design.
"""

import threading
from collections import OrderedDict
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Rough sizing note carried over from the pre-sharding roadmap doc: at a
# stress-tested scale of ~1000 households, ~100-150 concurrently-open SQLite
# files is a sane water mark. 128 keeps that same order of magnitude.
MAX_OPEN_HOUSEHOLD_DBS = 128


def _default_household_dir() -> Path:
    if settings.household_db_dir:
        return Path(settings.household_db_dir)
    if settings.database_url.startswith("sqlite:///"):
        # Sibling "households/" directory next to the shared file, so both
        # live inside the same Docker-mounted volume with zero extra config.
        shared_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
        return shared_db_path.parent / "households"
    # A non-SQLite database_url (e.g. Postgres) with no explicit override --
    # sharding here is specifically "one SQLite file per household", so this
    # fallback only matters for local dev/tests, not a real deployment choice.
    return BACKEND_DIR / "data" / "households"


HOUSEHOLD_DIR = _default_household_dir()

_lock = threading.Lock()
_registry: "OrderedDict[int, tuple[Engine, sessionmaker]]" = OrderedDict()


def household_db_path(household_id: int) -> Path:
    return HOUSEHOLD_DIR / f"{household_id}.db"


def run_household_migrations(db_path: Path) -> None:
    """Bring one household file's schema up to head, creating its parent
    directory first if needed (SQLite creates the file itself but not its
    parent directories -- callers other than _create_entry below, like the
    one-time split script, don't necessarily have a reason to have created
    HOUSEHOLD_DIR yet). Safe to call on an already-migrated file (no-op) --
    same idempotency guarantee as app/migrations.py's shared-stream
    run_migrations()."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic" / "household"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _create_entry(household_id: int) -> tuple[Engine, sessionmaker]:
    db_path = household_db_path(household_id)
    run_household_migrations(db_path)  # also ensures db_path.parent exists
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, session_local


def _get_or_create_entry(household_id: int) -> tuple[Engine, sessionmaker]:
    with _lock:
        entry = _registry.pop(household_id, None)
        if entry is not None:
            _registry[household_id] = entry  # re-insert: marks it most-recently-used
            return entry

        entry = _create_entry(household_id)
        _registry[household_id] = entry
        if len(_registry) > MAX_OPEN_HOUSEHOLD_DBS:
            _evicted_id, (evicted_engine, _evicted_session_local) = _registry.popitem(last=False)
            evicted_engine.dispose()
        return entry


def household_session(household_id: int) -> Session:
    """Open a new Session bound to one household's file, creating and
    migrating that file first if this is the first time it's been asked
    for. Caller owns the session and must close it -- the FastAPI
    dependency in app/dependencies.py does this automatically; direct
    callers (scripts, tests) need their own try/finally."""
    _engine, session_local = _get_or_create_entry(household_id)
    return session_local()


def reset_registry_for_tests() -> None:
    """Test-only: dispose every open household engine and forget them, so a
    fresh test doesn't reuse a connection pool pointed at a file the test
    fixture is about to delete/recreate. Disposing before the fixture
    removes the on-disk files (not after) matters -- an engine holding an
    open handle to an already-unlinked file is a real class of bug to avoid,
    not just tidiness."""
    with _lock:
        while _registry:
            _evicted_id, (evicted_engine, _evicted_session_local) = _registry.popitem()
            evicted_engine.dispose()
