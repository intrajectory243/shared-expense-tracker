from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import Household, User
from app.schemas import HouseholdOut, HouseholdUpdate

router = APIRouter(prefix="/households", tags=["households"])


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
