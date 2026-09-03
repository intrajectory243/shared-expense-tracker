from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.balances import get_cached_balance_summary, invalidate_balance_cache, purge_expired_trash
from app.database import get_db
from app.dependencies import get_current_admin, get_household_db, require_household
from app.models import Settlement, User
from app.schemas import BalanceSummary, SettlementCreate, SettlementOut

router = APIRouter(tags=["balances"])


@router.get("/balances", response_model=BalanceSummary)
def read_balances(
    user: User = Depends(require_household),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
):
    # Opportunistic trash cleanup rides on the balance read -- see
    # app/balances.py::purge_expired_trash. Runs before the (possibly cached)
    # summary; purging trashed rows never affects the balance result.
    purge_expired_trash(hh_db, user.household_id)
    return get_cached_balance_summary(hh_db, db, user.household_id)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
def create_settlement(
    payload: SettlementCreate,
    user: User = Depends(require_household),
    db: Session = Depends(get_db),
    hh_db: Session = Depends(get_household_db),
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
    hh_db.add(settlement)
    invalidate_balance_cache(hh_db, user.household_id)
    hh_db.commit()
    hh_db.refresh(settlement)
    return settlement


@router.get("/settlements", response_model=list[SettlementOut])
def list_settlements(
    include_deleted: bool = False,
    user: User = Depends(require_household),
    hh_db: Session = Depends(get_household_db),
):
    query = hh_db.query(Settlement).filter(Settlement.household_id == user.household_id)
    if not (include_deleted and user.is_admin):
        query = query.filter(Settlement.deleted_at.is_(None))
    return query.order_by(Settlement.date.desc(), Settlement.id.desc()).all()


@router.delete("/settlements/{settlement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_settlement(
    settlement_id: int,
    purge: bool = False,
    user: User = Depends(require_household),
    hh_db: Session = Depends(get_household_db),
):
    """Soft delete by default -- either party to the settlement or a household
    admin can do it. `?purge=true` (admin only) hard-deletes a row already in
    the trash. Mirrors DELETE /expenses/{id}."""
    settlement = (
        hh_db.query(Settlement)
        .filter(Settlement.id == settlement_id, Settlement.household_id == user.household_id)
        .first()
    )
    if not settlement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")

    if purge:
        if not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        if settlement.deleted_at is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Delete the settlement before purging it")
        hh_db.delete(settlement)
        hh_db.commit()
        return

    if settlement.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Settlement is already deleted")
    is_party = user.id in (settlement.from_user_id, settlement.to_user_id)
    if not is_party and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only a party to this settlement or an admin can delete it"
        )
    settlement.deleted_at = func.now()
    settlement.deleted_by_id = user.id
    invalidate_balance_cache(hh_db, user.household_id)
    hh_db.commit()


@router.post("/settlements/{settlement_id}/restore", response_model=SettlementOut)
def restore_settlement(
    settlement_id: int,
    admin: User = Depends(get_current_admin),
    hh_db: Session = Depends(get_household_db),
):
    settlement = (
        hh_db.query(Settlement)
        .filter(Settlement.id == settlement_id, Settlement.household_id == admin.household_id)
        .first()
    )
    if not settlement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")
    if settlement.deleted_at is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Settlement is not deleted")
    settlement.deleted_at = None
    settlement.deleted_by_id = None
    invalidate_balance_cache(hh_db, admin.household_id)
    hh_db.commit()
    hh_db.refresh(settlement)
    return settlement
