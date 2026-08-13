from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Household, User, UserRole, UserStatus
from app.schemas import Token, UserOut, UserSignup

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(payload: UserSignup, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    household = None
    if payload.household_name:
        household = Household(name=payload.household_name)
        db.add(household)
        db.flush()
    elif payload.household_id:
        household = db.query(Household).filter(Household.id == payload.household_id).first()
        if not household:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household not found")

    is_first_user = db.query(User).count() == 0
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        household_id=household.id if household else None,
        # First user on a fresh instance bootstraps as an approved admin so
        # a self-hosted install is usable immediately (Phase 2 auth notes).
        role=UserRole.admin if (is_first_user and settings.bootstrap_admin) else UserRole.member,
        status=UserStatus.approved if (is_first_user and settings.bootstrap_admin) else UserStatus.pending,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=user.email)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
