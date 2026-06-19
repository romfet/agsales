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

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


settings = Settings()
