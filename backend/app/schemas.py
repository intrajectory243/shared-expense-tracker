from datetime import date as date_type, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import UserRole, UserStatus


# ---- Auth / Users ----

class UserSignup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=120)
    household_name: str | None = Field(default=None, description="Create a new household")
    household_id: int | None = Field(default=None, description="Join an existing household (see GET /households)")


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    role: UserRole
    status: UserStatus
    household_id: int | None


class UserApprove(BaseModel):
    role: UserRole = UserRole.member


# ---- Households ----

class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


# ---- Expenses ----

class ExpenseCreate(BaseModel):
    amount: float = Field(gt=0)
    description: str = Field(min_length=1, max_length=255)
    category: str = "general"
    date: date_type | None = None
    participant_ids: list[int] = Field(min_length=1, description="Users this expense is split between")
    payer_id: int | None = Field(default=None, description="Defaults to the current user")


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: float
    description: str
    category: str
    date: date_type
    created_at: datetime
    payer: UserOut
    participants: list[UserOut]


# ---- Balances ----

class BalanceEntry(BaseModel):
    user_id: int
    name: str
    net: float  # positive = owed to them, negative = they owe


class DebtEntry(BaseModel):
    from_user_id: int
    from_name: str
    to_user_id: int
    to_name: str
    amount: float


class BalanceSummary(BaseModel):
    balances: list[BalanceEntry]
    settlements_to_make: list[DebtEntry]


# ---- Settlements ----

class SettlementCreate(BaseModel):
    to_user_id: int
    amount: float = Field(gt=0)
    date: date_type | None = None
    from_user_id: int | None = Field(default=None, description="Defaults to the current user")


class SettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_user_id: int
    to_user_id: int
    amount: float
    date: date_type
