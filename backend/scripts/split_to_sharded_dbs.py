"""One-time cutover for roadmap Phase 7: splits the single shared SQLite
file's expenses/expense_participants/settlements/balance_cache rows out
into one file per household (app/household_db.py). Run once, by hand,
after upgrading to a version of the app that expects the sharded layout --
NOT run automatically on startup (see app/migrations.py and
alembic/shared/versions/f60859b057c9's docstring for why the final drop
step here is deliberately raw SQL, not a routine migration).

Configuration matches the app itself -- same DATABASE_URL / HOUSEHOLD_DB_DIR
env vars, resolved the same way (see app/config.py, app/household_db.py).
Run this with whatever environment the real app startup would use, e.g.
inside the same Docker container:

    docker compose exec app python scripts/split_to_sharded_dbs.py

Safe to re-run from scratch if it fails before step 4 (the drop) -- steps
1-3 only ever copy, never modify, the original file. A backup of the
original file is made unconditionally before anything else happens.
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.household_db import household_db_path, run_household_migrations  # noqa: E402

# Drop order matters a little even with SQLite FK enforcement off by default
# (children before parents, as good practice) -- copy order inside
# _copy_household_rows is independent of this list.
MOVED_TABLES = ["expense_participants", "expenses", "settlements", "balance_cache"]


def _sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"This script only supports a SQLite database_url, got: {url}")
    return Path(url.removeprefix("sqlite:///"))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _copy_household_rows(shared_conn: sqlite3.Connection, household_id: int, household_path: Path) -> dict[str, int]:
    run_household_migrations(household_path)  # creates + migrates the target file first
    hh_conn = sqlite3.connect(household_path)
    counts: dict[str, int] = {}
    try:
        expense_rows = shared_conn.execute(
            "SELECT id, household_id, payer_id, created_by_id, amount, description, category, date, created_at "
            "FROM expenses WHERE household_id=?",
            (household_id,),
        ).fetchall()
        hh_conn.executemany(
            "INSERT INTO expenses (id, household_id, payer_id, created_by_id, amount, description, category, "
            "date, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            expense_rows,
        )
        counts["expenses"] = len(expense_rows)

        expense_ids = [row[0] for row in expense_rows]
        if expense_ids:
            placeholders = ",".join("?" for _ in expense_ids)
            participant_rows = shared_conn.execute(
                f"SELECT expense_id, user_id, share FROM expense_participants WHERE expense_id IN ({placeholders})",
                expense_ids,
            ).fetchall()
        else:
            participant_rows = []
        hh_conn.executemany(
            "INSERT INTO expense_participants (expense_id, user_id, share) VALUES (?,?,?)", participant_rows
        )
        counts["expense_participants"] = len(participant_rows)

        settlement_rows = shared_conn.execute(
            "SELECT id, household_id, from_user_id, to_user_id, amount, date, created_at "
            "FROM settlements WHERE household_id=?",
            (household_id,),
        ).fetchall()
        hh_conn.executemany(
            "INSERT INTO settlements (id, household_id, from_user_id, to_user_id, amount, date, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            settlement_rows,
        )
        counts["settlements"] = len(settlement_rows)

        cache_rows = shared_conn.execute(
            "SELECT household_id, payload, computed_at FROM balance_cache WHERE household_id=?", (household_id,)
        ).fetchall()
        hh_conn.executemany(
            "INSERT INTO balance_cache (household_id, payload, computed_at) VALUES (?,?,?)", cache_rows
        )
        counts["balance_cache"] = len(cache_rows)

        hh_conn.commit()
    finally:
        hh_conn.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    shared_path = _sqlite_path_from_url(settings.database_url)
    if not shared_path.exists():
        raise SystemExit(f"No shared database found at {shared_path} -- nothing to split.")

    conn = sqlite3.connect(shared_path)
    if not _table_exists(conn, "expenses"):
        print("Shared file has no 'expenses' table -- already split, or a genuinely fresh install. Nothing to do.")
        return

    households = conn.execute("SELECT id, name FROM households").fetchall()
    print(f"Found {len(households)} household(s) in {shared_path}.")

    if not args.yes:
        answer = input(
            "This copies data into per-household files, then drops the moved tables from the shared "
            "file. A backup of the original is made first either way. Continue? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("Aborted -- nothing was changed.")
            return

    # Step 1: back up the original file first -- non-negotiable.
    backup_path = shared_path.with_suffix(shared_path.suffix + ".pre-sharding-backup")
    shutil.copy2(shared_path, backup_path)
    print(f"Backed up original file to {backup_path}")

    # Step 2: copy each household's rows into its own new file.
    source_totals = {table: _row_count(conn, table) for table in MOVED_TABLES}
    copied_totals = {table: 0 for table in MOVED_TABLES}
    for household_id, name in households:
        target_path = household_db_path(household_id)
        print(f"Household {household_id} ({name}) -> {target_path}")
        counts = _copy_household_rows(conn, household_id, target_path)
        for table, n in counts.items():
            copied_totals[table] += n

    # Step 3: verify before touching the original -- this is the gate that
    # makes step 4 safe. Any mismatch aborts with the original file
    # untouched; delete the (incomplete) per-household files and re-run.
    mismatches = {
        table: (source_totals[table], copied_totals[table])
        for table in MOVED_TABLES
        if source_totals[table] != copied_totals[table]
    }
    if mismatches:
        raise SystemExit(
            f"Row count mismatch after copy, refusing to drop anything from the shared file: {mismatches}\n"
            "The original file is untouched. Delete the per-household files this run created and re-run."
        )
    print(f"Verified row counts match for all moved tables: {source_totals}")

    # Step 4: only now, drop the moved tables from the shared file.
    # Deliberately raw SQL, not an Alembic migration -- see this script's
    # module docstring for why.
    for table in MOVED_TABLES:
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    print("Dropped expenses/expense_participants/settlements/balance_cache from the shared file. Done.")


if __name__ == "__main__":
    main()
