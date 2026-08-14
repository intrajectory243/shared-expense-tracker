from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.balances import get_cached_balance_summary, invalidate_balance_cache
from app.database import get_db
from app.dependencies import require_household
from app.models import Settlement, User
from app.schemas import BalanceSummary, SettlementCreate, SettlementOut

router = APIRouter(tags=["balances"])


@router.get("/balances", response_model=BalanceSummary)
def read_balances(user: User = Depends(require_household), db: Session = Depends(get_db)):
    return get_cached_balance_summary(db, user.household_id)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
def create_settlement(
    payload: SettlementCreate, user: User = Depends(require_household), db: Session = Depends(get_db)
):
    from_user_id = payload.from_user_id or user.id
    if from_user_id == payload.to_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot settle up with yourself")
    # Whichever side of the payment actually happened can log it (the payer
    # marking "I paid", or the recipient marking "they paid me") -- but only
    # if you were one of the two parties, so no one can log on others' behalf.
    if user.id not in (from_user_id, payload.to_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only log a settlement you're a party to"
        )

    parties = (
        db.query(User)
        .filter(User.id.in_([from_user_id, payload.to_user_id]), User.household_id == user.household_id)
        .all()
    )
    if len(parties) != 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Both users must be in your household")

    settlement = Settlement(
        household_id=user.household_id,
        from_user_id=from_user_id,
        to_user_id=payload.to_user_id,
        amount=payload.amount,
        date=payload.date or date_type.today(),
    )
    db.add(settlement)
    invalidate_balance_cache(db, user.household_id)
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
