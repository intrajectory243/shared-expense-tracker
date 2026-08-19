import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import get_db
from app.dependencies import get_current_admin, get_current_user, require_household
from app.models import User, UserRole, UserStatus
from app.schemas import InviteCreate, InviteOut, UserApprove, UserLanguageUpdate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _admin_count(db: Session, household_id: int) -> int:
    return (
        db.query(User)
        .filter(User.household_id == household_id, User.role == UserRole.admin, User.status == UserStatus.approved)
        .count()
    )


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


@router.get("/former", response_model=list[UserOut])
def list_former_users(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Moved-out and fully-revoked members -- kept visible so their standing
    (owed / owing) doesn't just disappear when they leave."""
    return (
        db.query(User)
        .filter(
            User.household_id == admin.household_id,
            User.status.in_([UserStatus.moved_out, UserStatus.removed]),
        )
        .all()
    )


@router.patch("/me/language", response_model=UserOut)
def update_my_language(
    payload: UserLanguageUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Gated at the lowest auth tier (blocks only `removed`) so even a
    pending user can set their own language and see the pending-approval
    screen in it."""
    user.language = payload.language
    db.commit()
    db.refresh(user)
    return user


@router.post("/invite", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def invite_user(payload: InviteCreate, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    token = secrets.token_urlsafe(24)
    user = User(
        email=payload.email,
        name=payload.name,
        # Unusable until accept-invite sets a real one -- nobody can sign in
        # on this hash because it isn't derived from anything guessable.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=payload.role,
        status=UserStatus.pending,
        household_id=admin.household_id,
        invite_token=token,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return InviteOut(user=user, invite_token=token)


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


@router.patch("/{user_id}", response_model=UserOut)
def update_member(
    user_id: int,
    payload: UserUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Role and/or access changes for a member who's already been through
    approval once. Guard rails mirror the design: a household always keeps
    at least one admin, and an admin can't change their own access (only
    someone else can) -- self role changes are fine, self access changes
    aren't."""
    target = db.query(User).filter(User.id == user_id, User.household_id == admin.household_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.status == UserStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use PATCH /users/{id}/approve or DELETE for a pending request",
        )

    is_last_admin = (
        target.role == UserRole.admin
        and target.status == UserStatus.approved
        and _admin_count(db, admin.household_id) <= 1
    )

    if payload.role is not None and payload.role != target.role:
        if payload.role == UserRole.member and is_last_admin:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A household needs at least one admin")
        target.role = payload.role

    if payload.status is not None and payload.status != target.status:
        if payload.status == UserStatus.pending:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can't move a member back to pending")
        if payload.status != UserStatus.approved:
            if target.id == admin.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Ask another admin to change your own access"
                )
            if is_last_admin:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A household needs at least one admin")
            # Losing active status also loses admin rights; regained on return.
            target.role = UserRole.member
        target.status = payload.status

    db.commit()
    db.refresh(target)
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def decline_pending_user(user_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Declining is a hard delete -- safe only for pending requests, which by
    definition have no expenses tied to them yet."""
    target = (
        db.query(User)
        .filter(User.id == user_id, User.household_id == admin.household_id, User.status == UserStatus.pending)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending request not found")
    db.delete(target)
    db.commit()
