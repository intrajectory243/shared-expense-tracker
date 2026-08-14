from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import User, UserStatus

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    email = decode_access_token(token)
    if email is None:
        raise credentials_error
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_error
    if user.status == UserStatus.removed:
        # Blocks even an already-issued, still-unexpired token -- revoking
        # access takes effect immediately, not just for future logins.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account no longer has access to this household",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_approved_user(user: User = Depends(get_current_user)) -> User:
    # moved_out is intentionally allowed here: they can still read balance and
    # history and settle up, they just can't log or be tagged on new expenses
    # (enforced separately by get_current_active_user).
    if user.status not in (UserStatus.approved, UserStatus.moved_out):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is pending admin approval",
        )
    return user


def require_household(user: User = Depends(get_current_approved_user)) -> User:
    if user.household_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You are not part of a household yet")
    return user


def get_current_active_user(user: User = Depends(require_household)) -> User:
    """Stricter than require_household: excludes moved_out. Use for anything
    that logs a new expense or tags someone on one."""
    if user.status != UserStatus.approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You've moved out of this household and can't log new expenses",
        )
    return user


def get_current_admin(user: User = Depends(get_current_active_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
