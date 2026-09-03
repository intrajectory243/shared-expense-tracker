import enum
from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import HouseholdBase, SharedBase

# Expense/ExpenseParticipant/Settlement/BalanceCache (HouseholdBase) live in a
# separate SQLite file per household from User/Household/AppSetting/
# PushSubscription (SharedBase) -- see app/household_db.py. SQLite can't join
# or enforce a FOREIGN KEY across two files, and a SQLAlchemy relationship()
# can't span two engines either, so every id column that crosses that
# boundary below is a plain int, not a ForeignKey, and every relationship
# that would have crossed it has been removed. Router code fetches the
# other side's rows itself and stitches them on as plain (unmapped) instance
# attributes where a response needs them -- app/balances.py's
# get_balance_summary() already does exactly this and is the template.


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
    # Placeholder created by a household restore (roadmap Phase 8) for a user
    # id the restored file references that this instance doesn't know yet --
    # no usable password, can't sign in. Claimed automatically the moment
    # someone signs up with the email that hashes to this same id (see
    # app/routers/auth.py::signup) -- their real name/password fill in this
    # same row rather than a new one, so existing history stays attached.
    unclaimed = "unclaimed"


class Language(str, enum.Enum):
    en = "en"
    fa = "fa"


class Currency(str, enum.Enum):
    toman = "toman"
    rial = "rial"
    usd = "usd"
    eur = "eur"
    aed = "aed"


# Each expense has its own participant list, independent of who paid —
# this is what lets costs split only among whoever is actually tagged
# on that specific item (roadmap Phase 1). `share` is a weight, not a dollar
# amount: a participant with share=2 owes twice as much of the expense as
# one with share=1. Equal split (the default at creation) is just every
# tagged participant carrying share=1.
class ExpenseParticipant(HouseholdBase):
    __tablename__ = "expense_participants"

    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True)
    # Not a ForeignKey -- users.id lives in the shared file. Router code
    # resolves the User itself and stitches it on as expense.participants.
    # String, not int -- User.id is a uuid5(email) string (see app/identity.py).
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    share: Mapped[float] = mapped_column(Float, default=1.0)

    expense: Mapped["Expense"] = relationship(back_populates="participant_shares")


class Household(SharedBase):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[Currency] = mapped_column(Enum(Currency), default=Currency.toman, server_default="toman")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="household")
    # No `expenses`/`settlements` relationships -- those tables live in a
    # separate per-household file now; fetch them via app/household_db.py.


class User(SharedBase):
    __tablename__ = "users"

    # A deterministic uuid5(email) string, not an autoincrement integer --
    # see app/identity.py. Callers must pass id= explicitly when creating a
    # User (app/routers/auth.py, app/routers/users.py's invite_user); there's
    # no server-side default, since the id depends on the email being set.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.member)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.pending)
    language: Mapped[Language] = mapped_column(Enum(Language), default=Language.en, server_default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Set only for accounts created via an admin invite; cleared once the
    # invite is claimed. Distinguishes "invited" from organic sign-ups in
    # the pending list, and is the secret an accept-invite call must present.
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    household_id: Mapped[int | None] = mapped_column(ForeignKey("households.id"), nullable=True)
    household: Mapped[Household | None] = relationship(back_populates="users")

    # No `expenses_paid` relationship -- Expense lives in a separate
    # per-household file now.

    @property
    def is_approved(self) -> bool:
        return self.status == UserStatus.approved

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin

    @property
    def invited(self) -> bool:
        return self.invite_token is not None


class Expense(HouseholdBase):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # None of these three are ForeignKeys -- households.id and users.id both
    # live in the shared file. Router code resolves them itself and stitches
    # the results on as expense.household_id/.payer/.created_by/.participants
    # (the id columns already carry the plain int; .payer etc. are set as
    # plain instance attributes, not mapped columns -- see app/balances.py's
    # get_balance_summary() for the established pattern).
    household_id: Mapped[int] = mapped_column(index=True)
    # String, not int -- User.id is a uuid5(email) string (see app/identity.py).
    payer_id: Mapped[str] = mapped_column(String(36))
    # Who actually entered this record -- may differ from payer_id, since any
    # household member can log an expense on someone else's behalf. Always
    # set server-side from the authenticated user; never client-supplied.
    created_by_id: Mapped[str] = mapped_column(String(36))

    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(60), default="general")
    date: Mapped[date_type] = mapped_column(Date, default=date_type.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Soft delete: NULL = live, non-NULL = in the trash (an admin deleted it,
    # and an opportunistic purge will remove it for good once it's older than
    # settings.trash_retention_days). A soft-deleted expense drops out of
    # history, the balance math, and every non-admin view immediately;
    # restoring it (clearing these) puts it back exactly as it was.
    # deleted_by_id crosses into the shared file, so it's a plain str, not a FK.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)
    deleted_by_id: Mapped[str | None] = mapped_column(String(36), default=None)

    participant_shares: Mapped[list["ExpenseParticipant"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan"
    )

    # No `participants` property here anymore (it used to read `ps.user`,
    # which crosses into the shared file). Router code sets
    # `expense.participants = [...]` itself after resolving those users.


class Settlement(HouseholdBase):
    """Records a real-world repayment between two users, used to zero out a balance."""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not ForeignKeys -- see Expense above, same reasoning.
    household_id: Mapped[int] = mapped_column(index=True)
    # String, not int -- User.id is a uuid5(email) string (see app/identity.py).
    from_user_id: Mapped[str] = mapped_column(String(36))
    to_user_id: Mapped[str] = mapped_column(String(36))
    amount: Mapped[float] = mapped_column(Float)
    date: Mapped[date_type] = mapped_column(Date, default=date_type.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Soft delete -- see Expense.deleted_at. Either party or a household admin
    # can delete a settlement; only an admin can restore or purge it early.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)
    deleted_by_id: Mapped[str | None] = mapped_column(String(36), default=None)


class BalanceCache(HouseholdBase):
    """One cached GET /balances response per household, invalidated (deleted)
    by any write that could change it -- new expense, share edit, deleted
    expense, or settlement. A miss just recomputes and refills it, so this
    can never serve a wrong value, only a briefly absent one."""

    __tablename__ = "balance_cache"

    # Not a ForeignKey (see Expense above) -- and redundant with the file
    # boundary anyway, since a household's file only ever holds its own cache row.
    household_id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(HouseholdBase):
    """A household's own expense categories (roadmap Phase 9) -- lives here,
    not the shared file, so it travels with the household's own file on
    export/restore (Phase 8) the same way its expenses do. Expense.category
    stays a plain string, not a FK to this table's id: renaming a category
    is a bulk UPDATE of every matching Expense.category value in the same
    transaction (app/routers/categories.py), which is simpler than a join
    everywhere ExpenseOut.category is read, and this table is what tracks
    which names currently exist and their canonical spelling. Seeded
    lazily on first GET /categories, not at household-file creation time --
    same "nothing until it's actually needed" philosophy as the file itself."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not a ForeignKey (see Expense above) -- redundant with the file
    # boundary, kept only for consistency with every other HouseholdBase model.
    household_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(SharedBase):
    """Small instance-level key/value store for config generated at runtime
    (currently just the VAPID keypair for web push). Keeps a fresh
    self-hosted install zero-config while still persisting across restarts,
    since this table lives in the same SQLite file as everything else."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PushSubscription(SharedBase):
    """A browser's Web Push endpoint for one user. A user can have several
    (one per browser/device); an endpoint is unique across the instance, so
    re-subscribing the same browser under a different account just moves it."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(500), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Stays a real relationship -- User is shared too, so this is same-file.
    user: Mapped["User"] = relationship()
