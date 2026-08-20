"""The *shared* database -- one file for the whole instance, holding User,
Household, AppSetting, and PushSubscription. Expense/ExpenseParticipant/
Settlement/BalanceCache live in separate per-household files instead (see
app/household_db.py); this module only ever talks to the shared one."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

if is_sqlite:
    # WAL lets reads proceed without blocking on a concurrent writer (and
    # vice versa), instead of SQLite's default rollback-journal locking.
    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class SharedBase(DeclarativeBase):
    pass


class HouseholdBase(DeclarativeBase):
    """Separate declarative base (separate .metadata) for the four models
    that live in per-household files instead of the shared one. Kept here
    rather than in household_db.py so app/models.py has a single import
    site for both bases, and to avoid a models<->household_db circular
    import (household_db.py needs the models to run migrations/create_all)."""


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
