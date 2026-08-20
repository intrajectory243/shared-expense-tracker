import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Must be set before app.config/app.database/app.household_db are imported
# anywhere. Household files get their own temp directory, separate from the
# shared file, mirroring how they're separate in a real deployment.
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["HOUSEHOLD_DB_DIR"] = tempfile.mkdtemp(prefix="halves-household-dbs-")

import pytest
from fastapi.testclient import TestClient

from app.database import SharedBase, engine
from app.household_db import HOUSEHOLD_DIR, reset_registry_for_tests
from app.main import app


@pytest.fixture()
def client():
    SharedBase.metadata.drop_all(bind=engine)
    SharedBase.metadata.create_all(bind=engine)

    # Household files (Expense/ExpenseParticipant/Settlement/BalanceCache)
    # are created lazily per household_id the first time a test actually
    # hits a per-household route -- there's no fixed set of tables to
    # drop/create up front like the shared DB. Instead: dispose every
    # engine left open from the previous test (must happen before deleting
    # the files below), then wipe the whole directory clean.
    reset_registry_for_tests()
    if HOUSEHOLD_DIR.exists():
        shutil.rmtree(HOUSEHOLD_DIR)
    HOUSEHOLD_DIR.mkdir(parents=True, exist_ok=True)

    return TestClient(app)
