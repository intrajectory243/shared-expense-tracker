"""One-time cutover: converts payer_id/created_by_id/user_id/from_user_id/
to_user_id in every per-household file from the old integer user ids to
the new uuid5(email) ids (see app/identity.py and the shared-stream
Alembic migration alembic/shared/versions/a1c9f4d872be_*.py, which
converts users.id itself and is what actually computes the mapping this
script consumes).

Run once, by hand, on an existing install AFTER restarting into a version
of the app with the uuid migrations applied (run_migrations() applies
those automatically on startup, same as any other migration -- this
script only fixes up values in already-schema-migrated household files,
it doesn't run any Alembic migration itself):

    docker compose exec app python scripts/migrate_user_ids_to_uuid.py

A fresh install, or one with no existing users at the time it first
upgraded, never had a mapping to write in the first place -- this script
just reports there's nothing to do and exits.

Safe to re-run: every UPDATE only touches rows still holding an old
integer id, so a second pass over an already-converted file matches
nothing and changes nothing.
"""

import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.household_db import HOUSEHOLD_DIR  # noqa: E402

# expenses/settlements/expense_participants columns that hold a user id,
# per table -- see alembic/household/versions/b3e7291caa04_*.py.
_USER_ID_COLUMNS = {
    "expenses": ["payer_id", "created_by_id"],
    "settlements": ["from_user_id", "to_user_id"],
    "expense_participants": ["user_id"],
}


def _sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"This script only supports a SQLite database_url, got: {url}")
    return Path(url.removeprefix("sqlite:///"))


def _convert_one_file(path: Path, id_map: dict[int, str]) -> int:
    conn = sqlite3.connect(path)
    changed = 0
    try:
        for table, columns in _USER_ID_COLUMNS.items():
            for column in columns:
                for old_id, new_id in id_map.items():
                    # Matches whichever storage class the value actually has
                    # -- SQLite's TEXT-affinity coercion on the schema
                    # migration should already have turned it into text, but
                    # this stays correct even if a value was somehow left as
                    # a literal INTEGER.
                    cur = conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE {column}=? OR {column}=?",
                        (new_id, str(old_id), old_id),
                    )
                    changed += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return changed


def main() -> None:
    shared_path = _sqlite_path_from_url(settings.database_url)
    map_path = shared_path.with_suffix(shared_path.suffix + ".user-id-migration-map.json")
    if not map_path.exists():
        print("No user-id migration map found -- nothing to convert (fresh install, or already converted).")
        return

    raw_map: dict[str, str] = json.loads(map_path.read_text())
    id_map = {int(old_id): new_id for old_id, new_id in raw_map.items()}
    print(f"Loaded {len(id_map)} user id mapping(s) from {map_path}")

    household_files = sorted(HOUSEHOLD_DIR.glob("*.db"))
    print(f"Found {len(household_files)} household file(s) in {HOUSEHOLD_DIR}")

    for path in household_files:
        changed = _convert_one_file(path, id_map)
        print(f"  {path.name}: {changed} value(s) converted")

    map_path.unlink()
    print("Done -- migration map removed.")


if __name__ == "__main__":
    main()
