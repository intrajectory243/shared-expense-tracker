from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.balances import invalidate_balance_cache
from app.database import get_db
from app.dependencies import get_current_active_user, get_current_admin, get_household_db, require_household
from app.models import User, UserStatus, Expense, ExpenseParticipant
from app.push import send_to_users
from app.schemas import ExpenseCreate, ExpenseOut, ExpenseSharesUpdate

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _household_users_by_id(db: Session, household_id: int, user_ids: list[int]) -> dict[int, User]:
    # Only currently-active members are eligible: someone moved out or
    # removed can't be tagged on (or made payer of) a new expense, though
    # their existing expenses stay untouched.
    users = (
        db.query(User)
        .filter(User.id.in_(user_ids), User.household_id == household_id, User.status == UserStatus.approved)
        .all()
    )
    found = {u.id: u for u in users}
    missing = set(user_ids) - found.keys()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User id(s) {sorted(missing)} are not active members of your household",
        )
    return found


def _with_participant_shares(query):
    # participant_shares is lazy by default -- without eager-loading it here,
    # touching it per expense below turns this into one query per expense
    # (an N+1 that's invisible at a handful of expenses but costs seconds of
    # round-trip overhead once a household has hundreds+). This relationship
    # stays a real one (ExpenseParticipant lives in the same household file
    # as Expense) -- only the User side needs the manual stitch below.
    return query.options(selectinload(Expense.participant_shares))


def _stitch_expense_users(db: Session, expenses: list[Expense]) -> list[Expense]:
    """ExpenseOut needs payer/created_by/participants as full User objects
    (see schemas.py). Those relationships used to be ORM-automatic; now that
    Expense lives in a separate file from User, this fetches every needed
    User in one shared-DB query and assigns them as plain instance
    attributes -- Pydantic's from_attributes reads those exactly the same
    way it read relationship-backed ones, so ExpenseOut's shape is
    unchanged. Mirrors app/balances.py's get_balance_summary()."""
    user_ids: set[int] = set()
    for expense in expenses:
        user_ids.add(expense.payer_id)
        user_ids.add(expense.created_by_id)
        user_ids.update(ps.user_id for ps in expense.participant_shares)

    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    for expense in expenses:
        expense.payer = users_by_id.get(expense.payer_id)
        expense.created_by = users_by_id.get(expense.created_by_id)
        expense.participants = [
            users_by_id[ps.user_id] for ps in expense.participant_shares if ps.user_id in users_by_id
        ]
    return expenses


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(
    payload: ExpenseCreate,
    user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
):
    payer_id = payload.payer_id or user.id
    all_ids = set(payload.participant_ids) | {payer_id}
    users_by_id = _household_users_by_id(db, user.household_id, list(all_ids))

    expense = Expense(
        household_id=user.household_id,
        payer_id=payer_id,
        created_by_id=user.id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        date=payload.date or date_type.today(),
        participant_shares=[ExpenseParticipant(user_id=uid, share=1.0) for uid in payload.participant_ids],
    )
    hh_db.add(expense)
    invalidate_balance_cache(hh_db, user.household_id)
    hh_db.commit()
    hh_db.refresh(expense)

    expense.payer = users_by_id[payer_id]
    expense.created_by = user
    expense.participants = [users_by_id[uid] for uid in payload.participant_ids]

    other_member_ids = [
        uid
        for (uid,) in db.query(User.id)
        .filter(User.household_id == user.household_id, User.status == UserStatus.approved, User.id != user.id)
        .all()
    ]
    send_to_users(
        db,
        other_member_ids,
        title="New expense",
        body=f"{user.name} added “{expense.description}” — {expense.amount:,.0f} toman",
        url="/",
    )
    return expense


@router.get("", response_model=list[ExpenseOut])
def list_expenses(
    user: User = Depends(require_household),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
):
    expenses = (
        _with_participant_shares(hh_db.query(Expense))
        .filter(Expense.household_id == user.household_id)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .all()
    )
    return _stitch_expense_users(db, expenses)


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(
    expense_id: int,
    user: User = Depends(require_household),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
):
    expense = (
        _with_participant_shares(hh_db.query(Expense))
        .filter(Expense.id == expense_id, Expense.household_id == user.household_id)
        .first()
    )
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return _stitch_expense_users(db, [expense])[0]


@router.patch("/{expense_id}/shares", response_model=ExpenseOut)
def update_expense_shares(
    expense_id: int,
    payload: ExpenseSharesUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
):
    expense = hh_db.query(Expense).filter(Expense.id == expense_id, Expense.household_id == admin.household_id).first()
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")

    user_ids = [p.user_id for p in payload.participants]
    if len(set(user_ids)) != len(user_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Each participant can only appear once")
    _household_users_by_id(db, admin.household_id, user_ids)

    expense.participant_shares = [
        ExpenseParticipant(user_id=p.user_id, share=p.share) for p in payload.participants
    ]
    invalidate_balance_cache(hh_db, admin.household_id)
    hh_db.commit()
    hh_db.refresh(expense)
    return _stitch_expense_users(db, [expense])[0]


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: int,
    user: User = Depends(require_household),
    hh_db: Session = Depends(get_household_db),
):
    expense = hh_db.query(Expense).filter(Expense.id == expense_id, Expense.household_id == user.household_id).first()
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if expense.payer_id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the payer or an admin can delete this expense")
    hh_db.delete(expense)
    invalidate_balance_cache(hh_db, user.household_id)
    hh_db.commit()
