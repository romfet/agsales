"""Application settings, loaded from environment / backend/.env."""
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ClientCred(BaseModel):
    """A registered machine-to-machine client (OAuth2 client credentials)."""

    client_id: str
    client_secret: str
    scopes: list[str] = []


class Settings(BaseSettings):
    # Works for both sync (Alembic) and async (app) engines — psycopg3 supports both.
    database_url: str = "postgresql+psycopg://agsales:agsales@localhost:5432/agsales"

    # 1C HTTP-service (used from phase 2)
    onec_base_url: str = ""
    onec_user: str = ""
    onec_password: str = ""

    # --- App-to-app auth (OAuth2 client credentials → HS256 JWT) ---
    # Empty secret = auth DISABLED (local/dev only). Production MUST set it.
    auth_jwt_secret: str = ""
    auth_token_ttl_seconds: int = 3600
    # Registered M2M clients. Set via AUTH_CLIENTS as JSON, e.g.:
    #   AUTH_CLIENTS='[{"client_id":"onec","client_secret":"…","scopes":["ingest"]},
    #                  {"client_id":"operator","client_secret":"…","scopes":["analyze"]}]'
    auth_clients: list[ClientCred] = []

    # Aggregate-refresh cadence for the worker (push model: ingest writes raw
    # rows, the worker rebuilds materialized views on this interval).
    refresh_interval_minutes: int = 10

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


settings = Settings()
