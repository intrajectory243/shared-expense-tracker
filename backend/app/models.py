import enum
from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    member = "member"


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"


# Each expense has its own participant list, independent of who paid —
# this is what lets costs split only among whoever is actually tagged
# on that specific item (roadmap Phase 1).
expense_participants = Table(
    "expense_participants",
    Base.metadata,
    Column("expense_id", ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


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

    household_id: Mapped[int | None] = mapped_column(ForeignKey("households.id"), nullable=True)
    household: Mapped[Household | None] = relationship(back_populates="users")

    expenses_paid: Mapped[list["Expense"]] = relationship(back_populates="payer")
    expenses_participated: Mapped[list["Expense"]] = relationship(
        secondary=expense_participants, back_populates="participants"
    )

    @property
    def is_approved(self) -> bool:
        return self.status == UserStatus.approved

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(60), default="general")
    date: Mapped[date_type] = mapped_column(Date, default=date_type.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    household: Mapped[Household] = relationship(back_populates="expenses")
    payer: Mapped[User] = relationship(back_populates="expenses_paid")
    participants: Mapped[list[User]] = relationship(
        secondary=expense_participants, back_populates="expenses_participated"
    )


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
