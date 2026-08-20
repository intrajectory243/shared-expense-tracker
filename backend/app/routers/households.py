import re
import secrets
import shutil
import sqlite3
from datetime import date as date_type
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.auth import hash_password
from app.database import get_db
from app.dependencies import get_current_admin
from app.household_db import HOUSEHOLD_DIR, evict_household, household_db_path, run_household_migrations
from app.models import Household, User, UserRole, UserStatus
from app.schemas import HouseholdOut, HouseholdUpdate, RestoreSummary

router = APIRouter(prefix="/households", tags=["households"])

# Roadmap Phase 8 (backup/restore). expenses/settlements carry their own
# household_id column (rewritten on restore, see restore_household);
# balance_cache's PK *is* household_id, so it's excluded from that rewrite
# and just dropped after restore instead (recomputed fresh on next read).
_HOUSEHOLD_ID_TABLES = ["expenses", "settlements"]
# Every column, per table, that holds a user id -- used to find every user
# a restored file references so an unclaimed stub can be created for any
# this instance doesn't already know (see app/routers/auth.py::signup for
# the other half, claiming a stub on a matching real signup).
_USER_ID_COLUMNS = {
    "expenses": ["payer_id", "created_by_id"],
    "settlements": ["from_user_id", "to_user_id"],
    "expense_participants": ["user_id"],
}


class _InvalidBackupFile(Exception):
    pass


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "household"


def _validate_and_upgrade_restore_file(path: Path) -> None:
    """Raises _InvalidBackupFile if `path` isn't a real, intact household
    backup. Upgrading it to the current schema (rather than rejecting an
    older one) is what run_household_migrations already does for every
    existing household file on every app startup -- reused as-is here."""
    try:
        conn = sqlite3.connect(path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            has_version_table = (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise _InvalidBackupFile("Not a valid SQLite database") from exc
    if not result or result[0] != "ok":
        raise _InvalidBackupFile("Uploaded file failed its integrity check")

    # A blank/empty SQLite file would otherwise sail through
    # run_household_migrations below (nothing to conflict with, so Alembic
    # just creates the schema fresh) -- requiring pre-existing migration
    # history is what actually pins this down to "a real prior export",
    # not just "something SQLite can open".
    if not has_version_table:
        raise _InvalidBackupFile("Not a recognized household backup file")

    try:
        run_household_migrations(path)
    except Exception as exc:
        # Most likely: alembic_version names a revision from the *shared*
        # stream (or nothing this stream's graph recognizes at all) -- i.e.
        # this was never a per-household export in the first place.
        raise _InvalidBackupFile("Not a recognized household backup file") from exc


@router.get("", response_model=list[HouseholdOut])
def list_households(db: Session = Depends(get_db)):
    """Public id/name listing so a new signup can pick which household to request joining."""
    return db.query(Household).all()


@router.patch("/{household_id}", response_model=HouseholdOut)
def rename_household(
    household_id: int,
    payload: HouseholdUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if household_id != admin.household_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your household")
    household = db.query(Household).filter(Household.id == household_id).first()
    if payload.name is not None:
        household.name = payload.name.strip()
    if payload.currency is not None:
        household.currency = payload.currency
    db.commit()
    db.refresh(household)
    return household


@router.get("/{household_id}/export")
def export_household(
    household_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if household_id != admin.household_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your household")
    household = db.query(Household).filter(Household.id == household_id).first()

    src_path = household_db_path(household_id)
    if not src_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This household has no data yet")

    # A plain file copy could miss rows still sitting in the WAL sidecar
    # that haven't been checkpointed (app/household_db.py runs every
    # household file in WAL mode) -- sqlite3's online backup API produces
    # a point-in-time-consistent snapshot regardless, without needing to
    # touch the live engine or its connection pool at all.
    tmp_path = HOUSEHOLD_DIR / f"{household_id}.export-{secrets.token_hex(8)}.db"
    src_conn = sqlite3.connect(src_path)
    dst_conn = sqlite3.connect(tmp_path)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    filename = f"{_slug(household.name)}-{date_type.today().isoformat()}.db"
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename=filename,
        background=BackgroundTask(tmp_path.unlink),
    )


@router.post("/{household_id}/restore", response_model=RestoreSummary)
def restore_household(
    household_id: int,
    file: UploadFile,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Swaps this household's Expense/ExpenseParticipant/Settlement/
    BalanceCache file for the uploaded one -- see the roadmap Phase 8
    design notes (TECHNICAL_OVERVIEW.md / the project memory on this) for
    why this needs the unclaimed-stub identity dance and not just a raw
    file overwrite."""
    if household_id != admin.household_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your household")

    real_path = household_db_path(household_id)
    HOUSEHOLD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = HOUSEHOLD_DIR / f"{household_id}.restore-{secrets.token_hex(8)}.db"
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        _validate_and_upgrade_restore_file(tmp_path)
    except _InvalidBackupFile as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    conn = sqlite3.connect(tmp_path)
    try:
        # Restoring my own backup, and "migrate this household's data to a
        # fresh self-hosted instance" (a different household id there), are
        # the same operation from here on: whatever household_id the file
        # was originally exported under, every row becomes this admin's
        # household from this point forward.
        for table in _HOUSEHOLD_ID_TABLES:
            conn.execute(f"UPDATE {table} SET household_id=?", (household_id,))
        conn.commit()

        referenced_ids: set[str] = set()
        for table, columns in _USER_ID_COLUMNS.items():
            for column in columns:
                referenced_ids.update(
                    row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()
                )
        expenses_restored = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        settlements_restored = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    finally:
        conn.close()

    known_ids = {uid for (uid,) in db.query(User.id).filter(User.id.in_(referenced_ids)).all()}
    unknown_ids = referenced_ids - known_ids
    for uid in unknown_ids:
        db.add(
            User(
                id=uid,
                # Not ".invalid"/".local"/".test" -- those are RFC 2606/6762
                # reserved names that pydantic's EmailStr rejects outright
                # (UserOut would then fail to serialize this stub in any
                # response, e.g. a household's expense list, the moment one
                # exists).
                email=f"unclaimed-{uid}@unclaimed.internal",
                # Unusable until a real signup claims this row -- nobody
                # can sign in on this hash, same trick as invite_user's.
                password_hash=hash_password(secrets.token_urlsafe(32)),
                name="Unknown member",
                role=UserRole.member,
                status=UserStatus.unclaimed,
                household_id=household_id,
            )
        )
    db.commit()

    if real_path.exists():
        # Unconditional, before anything below can touch the live file --
        # same precedent as backend/scripts/split_to_sharded_dbs.py.
        backup_path = real_path.with_suffix(real_path.suffix + ".pre-restore-backup")
        shutil.copy2(real_path, backup_path)

    # Drop any pooled connection this household's engine is holding before
    # the file underneath it changes -- otherwise a request racing this one
    # could keep reading/writing through stale WAL state after the swap.
    evict_household(household_id)

    tmp_path.replace(real_path)
    for suffix in ("-wal", "-shm"):
        stale = Path(f"{real_path}{suffix}")
        if stale.exists():
            stale.unlink()

    conn = sqlite3.connect(real_path)
    try:
        conn.execute("DELETE FROM balance_cache")
        conn.commit()
    finally:
        conn.close()

    return RestoreSummary(
        expenses_restored=expenses_restored,
        settlements_restored=settlements_restored,
        unclaimed_users_created=len(unknown_ids),
    )
