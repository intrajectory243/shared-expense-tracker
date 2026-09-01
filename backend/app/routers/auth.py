from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.identity import user_uuid
from app.models import Household, User, UserRole, UserStatus
from app.push import send_to_users
from app.schemas import AcceptInvite, Token, UserOut, UserSignup

router = APIRouter(prefix="/auth", tags=["auth"])


def _initial_role_and_status(db: Session, household: Household | None) -> tuple[UserRole, UserStatus]:
    """Whoever founds a household is its admin. A household with no approved
    admin is unusable -- nobody can approve joiners -- so the first person
    into one (a fresh household they just created, or an existing one whose
    admins are all gone) is auto-approved as admin. Everyone else joining an
    existing household waits for approval. ``BOOTSTRAP_ADMIN=false`` opts the
    whole instance out of this and expects an admin to be set out-of-band.
    """
    if household is None:
        return UserRole.member, UserStatus.pending

    household_has_admin = (
        db.query(User.id)
        .filter(
            User.household_id == household.id,
            User.role == UserRole.admin,
            User.status == UserStatus.approved,
        )
        .first()
        is not None
    )
    if settings.bootstrap_admin and not household_has_admin:
        return UserRole.admin, UserStatus.approved
    return UserRole.member, UserStatus.pending


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(payload: UserSignup, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    # A household restore (roadmap Phase 8) may have already created an
    # unclaimed stub at this exact id (uuid5 is deterministic on email) for
    # a user this instance didn't know yet. Claim it in place -- fill in
    # the real name/password on that same row -- rather than inserting a
    # second row at an id that's already taken; every expense/settlement
    # that already pointed at this id (via the stub) stays attached, and
    # now resolves to the real name through the usual stitch. A claim
    # never creates or joins a household from payload -- the stub's
    # household_id is already the one its restored history actually lives
    # in, so payload.household_name/household_id are ignored entirely for
    # this branch (and never touched, so nothing gets created/looked up).
    uid = user_uuid(payload.email)
    stub = db.get(User, uid)

    if stub is not None and stub.status == UserStatus.unclaimed:
        user = stub
        user.email = payload.email
        user.password_hash = hash_password(payload.password)
        user.name = payload.name
        user.language = payload.language
        household = db.get(Household, user.household_id) if user.household_id else None
        user.role, user.status = _initial_role_and_status(db, household)
    else:
        household = None
        if payload.household_name:
            household = Household(name=payload.household_name, currency=payload.household_currency)
            db.add(household)
            db.flush()
        elif payload.household_id:
            household = db.query(Household).filter(Household.id == payload.household_id).first()
            if not household:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household not found")

        role, new_status = _initial_role_and_status(db, household)
        user = User(
            id=uid,
            email=payload.email,
            password_hash=hash_password(payload.password),
            name=payload.name,
            language=payload.language,
            household_id=household.id if household else None,
            role=role,
            status=new_status,
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    if user.status == UserStatus.pending and household is not None:
        admin_ids = [
            aid
            for (aid,) in db.query(User.id)
            .filter(
                User.household_id == household.id, User.role == UserRole.admin, User.status == UserStatus.approved
            )
            .all()
        ]
        send_to_users(
            db,
            admin_ids,
            title="New approval request",
            body=f"{user.name} wants to join {household.name}",
            url="/",
        )
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
    if user.status == UserStatus.removed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account no longer has access to this household. Ask a household admin to restore it.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=user.email)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/invite/{token}")
def preview_invite(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.invite_token == token).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found or already used")
    return {"name": user.name, "email": user.email, "household_name": user.household.name}


@router.post("/accept-invite", response_model=UserOut)
def accept_invite(payload: AcceptInvite, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.invite_token == payload.token).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found or already used")
    # An invite means an admin already vetted them, so accepting it skips the
    # usual pending-approval gate.
    user.password_hash = hash_password(payload.password)
    user.status = UserStatus.approved
    user.invite_token = None
    db.commit()
    db.refresh(user)
    return user
