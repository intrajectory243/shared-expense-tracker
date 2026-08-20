from datetime import date as date_type, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Currency, Language, UserRole, UserStatus


# ---- Auth / Users ----

class UserSignup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=120)
    household_name: str | None = Field(default=None, description="Create a new household")
    household_id: int | None = Field(default=None, description="Join an existing household (see GET /households)")
    language: Language = Language.en
    household_currency: Currency = Field(
        default=Currency.toman, description="Only applied when creating a new household; ignored when joining one"
    )


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    name: str
    role: UserRole
    status: UserStatus
    language: Language
    household_id: int | None
    invited: bool = False


class UserApprove(BaseModel):
    role: UserRole = UserRole.member


class UserUpdate(BaseModel):
    """Role and/or access changes for an already-approved (or former) member.

    Pending sign-ups use /approve instead; this never moves someone into or
    out of 'pending'.
    """

    role: UserRole | None = None
    status: UserStatus | None = None


class UserLanguageUpdate(BaseModel):
    language: Language


class InviteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: UserRole = UserRole.member


class InviteOut(BaseModel):
    user: UserOut
    invite_token: str


class AcceptInvite(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=72)


# ---- Households ----

class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    currency: Currency


class HouseholdUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    currency: Currency | None = None


# ---- Expenses ----

class ExpenseCreate(BaseModel):
    amount: float = Field(gt=0)
    description: str = Field(min_length=1, max_length=255)
    category: str = "general"
    date: date_type | None = None
    participant_ids: list[str] = Field(min_length=1, description="Users this expense is split between")
    payer_id: str | None = Field(default=None, description="Defaults to the current user")


class ExpenseShare(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    share: float = Field(gt=0)


class ExpenseSharesUpdate(BaseModel):
    participants: list[ExpenseShare] = Field(min_length=1)


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: float
    description: str
    category: str
    date: date_type
    created_at: datetime
    payer: UserOut
    created_by: UserOut
    participants: list[UserOut]
    shares: list[ExpenseShare] = Field(validation_alias="participant_shares")


# ---- Balances ----

class BalanceEntry(BaseModel):
    user_id: str
    name: str
    net: float  # positive = owed to them, negative = they owe


class DebtEntry(BaseModel):
    from_user_id: str
    from_name: str
    to_user_id: str
    to_name: str
    amount: float


class BalanceSummary(BaseModel):
    balances: list[BalanceEntry]
    settlements_to_make: list[DebtEntry]


# ---- Push notifications ----

class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]


class PushUnsubscribe(BaseModel):
    endpoint: str


class VapidKeyOut(BaseModel):
    public_key: str


# ---- Settlements ----

class SettlementCreate(BaseModel):
    to_user_id: str
    amount: float = Field(gt=0)
    date: date_type | None = None
    from_user_id: str | None = Field(default=None, description="Defaults to the current user")


class SettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_user_id: str
    to_user_id: str
    amount: float
    date: date_type
