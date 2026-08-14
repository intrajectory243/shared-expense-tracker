import enum
from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    member = "member"


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    # Left the household. Their expenses/settlements stay in history and in
    # the balance math either way -- these two only differ in sign-in access.
    moved_out = "moved_out"  # can still sign in: read balance/history, settle up, but not log/be tagged on new expenses
    removed = "removed"  # sign-in refused entirely, including on an already-issued token


# Each expense has its own participant list, independent of who paid —
# this is what lets costs split only among whoever is actually tagged
# on that specific item (roadmap Phase 1). `share` is a weight, not a dollar
# amount: a participant with share=2 owes twice as much of the expense as
# one with share=1. Equal split (the default at creation) is just every
# tagged participant carrying share=1.
class ExpenseParticipant(Base):
    __tablename__ = "expense_participants"

    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    share: Mapped[float] = mapped_column(Float, default=1.0)

    expense: Mapped["Expense"] = relationship(back_populates="participant_shares")
    user: Mapped["User"] = relationship()


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="household")
    expenses: Mapped[list["Expense"]] = relationship(back_populates="household")
    settlements: Mapped[list["Settlement"]] = relationship(back_populates="household")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.member)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Set only for accounts created via an admin invite; cleared once the
    # invite is claimed. Distinguishes "invited" from organic sign-ups in
    # the pending list, and is the secret an accept-invite call must present.
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    household_id: Mapped[int | None] = mapped_column(ForeignKey("households.id"), nullable=True)
    household: Mapped[Household | None] = relationship(back_populates="users")

    expenses_paid: Mapped[list["Expense"]] = relationship(
        foreign_keys="Expense.payer_id", back_populates="payer"
    )

    @property
    def is_approved(self) -> bool:
        return self.status == UserStatus.approved

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin

    @property
    def invited(self) -> bool:
        return self.invite_token is not None


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Who actually entered this record -- may differ from payer_id, since any
    # household member can log an expense on someone else's behalf. Always
    # set server-side from the authenticated user; never client-supplied.
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(60), default="general")
    date: Mapped[date_type] = mapped_column(Date, default=date_type.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    household: Mapped[Household] = relationship(back_populates="expenses")
    payer: Mapped[User] = relationship(foreign_keys=[payer_id], back_populates="expenses_paid")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    participant_shares: Mapped[list["ExpenseParticipant"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan"
    )

    @property
    def participants(self) -> list[User]:
        return [ps.user for ps in self.participant_shares]


class Settlement(Base):
    """Records a real-world repayment between two users, used to zero out a balance."""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float)
    date: Mapped[date_type] = mapped_column(Date, default=date_type.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    household: Mapped[Household] = relationship(back_populates="settlements")
    from_user: Mapped[User] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])


class BalanceCache(Base):
    """One cached GET /balances response per household, invalidated (deleted)
    by any write that could change it -- new expense, share edit, deleted
    expense, or settlement. A miss just recomputes and refills it, so this
    can never serve a wrong value, only a briefly absent one."""

    __tablename__ = "balance_cache"

    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    """Small instance-level key/value store for config generated at runtime
    (currently just the VAPID keypair for web push). Keeps a fresh
    self-hosted install zero-config while still persisting across restarts,
    since this table lives in the same SQLite file as everything else."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PushSubscription(Base):
    """A browser's Web Push endpoint for one user. A user can have several
    (one per browser/device); an endpoint is unique across the instance, so
    re-subscribing the same browser under a different account just moves it."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(500), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship()
