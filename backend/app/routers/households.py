from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Household
from app.schemas import HouseholdOut

router = APIRouter(prefix="/households", tags=["households"])


@router.get("", response_model=list[HouseholdOut])
def list_households(db: Session = Depends(get_db)):
    """Public id/name listing so a new signup can pick which household to request joining."""
    return db.query(Household).all()
