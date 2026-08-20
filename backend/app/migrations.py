from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_shared_migrations() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic" / "shared"))
    command.upgrade(cfg, "head")


def _run_household_migrations_for_existing_files() -> None:
    # A brand-new household file gets migrated to head the moment it's
    # first created too (app/household_db.py::run_household_migrations,
    # called from _create_entry) -- this only needs to catch up files that
    # already existed before this deploy, e.g. after a schema change to the
    # household stream ships.
    from app.household_db import HOUSEHOLD_DIR, run_household_migrations

    if not HOUSEHOLD_DIR.is_dir():
        return
    for db_file in sorted(HOUSEHOLD_DIR.glob("*.db")):
        run_household_migrations(db_file)


def run_migrations() -> None:
    """Bring both migration streams up to date on startup -- self-hosters
    just `git pull` and restart, no manual migration command required.
    Deliberately does NOT drop the four tables the household stream now
    owns from the shared file if they're still there -- see
    alembic/shared/versions/f60859b057c9's docstring and
    backend/scripts/split_to_sharded_dbs.py for why that's a one-time,
    hand-run script step instead of something routine startup does."""
    _run_shared_migrations()
    _run_household_migrations_for_existing_files()
