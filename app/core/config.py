from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "anuvia"
    APP_ENV: str = "development"
    DEBUG: bool = False

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    DATABASE_URL: str = "sqlite+aiosqlite:///./local.db"

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Comma-separated list of browser origins allowed to call this API, or "*"
    # for any. "*" is right for local development and wrong for production:
    # anything on the web can then read a response on a visitor's behalf. Set
    # the real frontend origins before you ship.
    CORS_ALLOW_ORIGINS: str = "*"

    # --- playroom ---------------------------------------------------------
    # Shared secret the retention sweeper expects as a bearer token. Empty
    # disables the endpoint entirely, which is the safe default: an unguarded
    # sweep endpoint lets anyone anonymise a live room's players.
    PLAYROOM_MAINTENANCE_TOKEN: str = ""
    # Hours a room stays reachable after its last change. On screen in the
    # client ("keys stop working two hours after the last round"), so changing
    # it means changing that copy too.
    PLAYROOM_ROOM_TTL_HOURS: int = 2
    # Days before the sweeper drops nicknames, boards and selections. The rows
    # and their ids stay, so every aggregate remains correct.
    PLAYROOM_RETENTION_DAYS: int = 7

    # Set by the deploy workflow to the UTC time of the deploy (ISO-8601).
    # Empty in local development. Surfaced by /health, formatted in IST.
    DEPLOYED_AT: str = ""

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cors_origins(self) -> list[str]:
        """`CORS_ALLOW_ORIGINS` as the list Starlette wants."""
        return [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]


settings = Settings()
