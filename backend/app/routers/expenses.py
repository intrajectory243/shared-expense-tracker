from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_household
from app.models import Expense, User
from app.schemas import ExpenseCreate, ExpenseOut

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _household_users_by_id(db: Session, household_id: int, user_ids: list[int]) -> dict[int, User]:
    users = db.query(User).filter(User.id.in_(user_ids), User.household_id == household_id).all()
    found = {u.id: u for u in users}
    missing = set(user_ids) - found.keys()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User id(s) {sorted(missing)} are not members of your household",
        )
    return found


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(payload: ExpenseCreate, user: User = Depends(require_household), db: Session = Depends(get_db)):
    payer_id = payload.payer_id or user.id
    all_ids = set(payload.participant_ids) | {payer_id}
    users_by_id = _household_users_by_id(db, user.household_id, list(all_ids))

    expense = Expense(
        household_id=user.household_id,
        payer_id=payer_id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        date=payload.date or date_type.today(),
        participants=[users_by_id[uid] for uid in payload.participant_ids],
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("", response_model=list[ExpenseOut])
def list_expenses(user: User = Depends(require_household), db: Session = Depends(get_db)):
    return (
        db.query(Expense)
        .filter(Expense.household_id == user.household_id)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .all()
    )


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, user: User = Depends(require_household), db: Session = Depends(get_db)):
    expense = db.query(Expense).filter(Expense.id == expense_id, Expense.household_id == user.household_id).first()
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return expense


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(expense_id: int, user: User = Depends(require_household), db: Session = Depends(get_db)):
    expense = db.query(Expense).filter(Expense.id == expense_id, Expense.household_id == user.household_id).first()
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if expense.payer_id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the payer or an admin can delete this expense")
    db.delete(expense)
    db.commit()
