from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./expense_tracker.db"
    secret_key: str = "dev-secret-key-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    algorithm: str = "HS256"

    # First user to sign up is auto-approved as admin so the instance is usable
    # immediately after a fresh self-hosted install.
    bootstrap_admin: bool = True


settings = Settings()
