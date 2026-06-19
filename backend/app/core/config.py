"""Application settings, loaded from environment / backend/.env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # Works for both sync (Alembic) and async (app) engines — psycopg3 supports both.
    database_url: str = "postgresql+psycopg://agsales:agsales@localhost:5432/agsales"

    # 1C HTTP-service (used from phase 2)
    onec_base_url: str = ""
    onec_user: str = ""
    onec_password: str = ""

    # API auth — placeholder bearer token pending the SSO/OIDC decision.
    # Empty = open (dev only). MUST be set in production until SSO lands.
    api_auth_token: str = ""
    # CORS origins for the React SPA (comma-separated). "*" in dev.
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


settings = Settings()
