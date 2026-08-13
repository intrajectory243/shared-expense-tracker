from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin, require_household
from app.models import User, UserStatus
from app.schemas import UserApprove, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_household_users(user: User = Depends(require_household), db: Session = Depends(get_db)):
    return db.query(User).filter(User.household_id == user.household_id, User.status == UserStatus.approved).all()


@router.get("/pending", response_model=list[UserOut])
def list_pending_users(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return (
        db.query(User)
        .filter(User.household_id == admin.household_id, User.status == UserStatus.pending)
        .all()
    )


@router.patch("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int,
    payload: UserApprove,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.household_id != admin.household_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not in your household")

    target.status = UserStatus.approved
    target.role = payload.role
    db.commit()
    db.refresh(target)
    return target
