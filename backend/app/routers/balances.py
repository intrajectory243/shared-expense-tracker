from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.balances import get_balance_summary
from app.database import get_db
from app.dependencies import require_household
from app.models import Settlement, User
from app.schemas import BalanceSummary, SettlementCreate, SettlementOut

router = APIRouter(tags=["balances"])


@router.get("/balances", response_model=BalanceSummary)
def read_balances(user: User = Depends(require_household), db: Session = Depends(get_db)):
    return get_balance_summary(db, user.household_id)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
def create_settlement(
    payload: SettlementCreate, user: User = Depends(require_household), db: Session = Depends(get_db)
):
    if payload.to_user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot settle up with yourself")
    to_user = (
        db.query(User).filter(User.id == payload.to_user_id, User.household_id == user.household_id).first()
    )
    if not to_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recipient is not in your household")

    settlement = Settlement(
        household_id=user.household_id,
        from_user_id=user.id,
        to_user_id=payload.to_user_id,
        amount=payload.amount,
        date=payload.date or date_type.today(),
    )
    db.add(settlement)
    db.commit()
    db.refresh(settlement)
    return settlement


@router.get("/settlements", response_model=list[SettlementOut])
def list_settlements(user: User = Depends(require_household), db: Session = Depends(get_db)):
    return (
        db.query(Settlement)
        .filter(Settlement.household_id == user.household_id)
        .order_by(Settlement.date.desc(), Settlement.id.desc())
        .all()
    )
