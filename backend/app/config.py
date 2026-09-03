from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./expense_tracker.db"
    # Directory holding one SQLite file per household (Expense/ExpenseParticipant/
    # Settlement/BalanceCache -- see app/household_db.py). Defaults to a
    # "households/" sibling next to database_url's own file so both live in the
    # same Docker-mounted volume with zero extra config.
    household_db_dir: str | None = None
    secret_key: str = "dev-secret-key-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    algorithm: str = "HS256"

    # The first user into any household with no approved admin (a household
    # they just created, or one whose admins are all gone) is auto-approved
    # as its admin, so a fresh install -- and every household created on a
    # multi-tenant instance -- is usable immediately. Set false to opt the
    # whole instance out and assign admins out-of-band instead.
    bootstrap_admin: bool = True

    # A deleted expense/settlement lingers in the trash this many days so an
    # admin can undo a mistake, then an opportunistic purge (triggered on the
    # household's next balance read or expense list) removes it for good.
    trash_retention_days: int = 30


settings = Settings()
