"""Application settings, loaded from environment / backend/.env."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ClientCred(BaseModel):
    """A registered app-to-app client: id + secret + what it may do."""

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

    # --- App-to-app auth: client POSTs its secret to /api/auth/token, gets a
    #     server-signed JWT, then calls protected endpoints only with that token. ---
    # Empty signing secret = auth OPEN (local/dev only). Production MUST set it.
    auth_jwt_secret: str = ""          # server HS256 signing key
    auth_token_ttl_seconds: int = 3600
    # Registered clients (id + secret + scopes), via AUTH_CLIENTS env as JSON:
    #   AUTH_CLIENTS='[{"client_id":"onec","client_secret":"…","scopes":["ingest"]},
    #                  {"client_id":"operator","client_secret":"…","scopes":["analyze"]}]'
    auth_clients: list[ClientCred] = []

    # Aggregate-refresh cadence for the worker (push model: ingest writes raw
    # rows, the worker rebuilds materialized views on this interval).
    refresh_interval_minutes: int = 10

    # --- Приём данных 1С (SDD_сборка_документов_при_приёме.md) ---
    # direct — каждый запрос заменяет документ целиком (прежнее поведение);
    # staged — пачки копятся в staging и собираются worker'ом по документам.
    ingest_mode: Literal["direct", "staged"] = "direct"
    # keep — позиции, которых нет в загрузке, сохраняются; replace — удаляются;
    # replace_on_commit — удаляются только в сборке по commit.
    ingest_shrink_mode: Literal["keep", "replace", "replace_on_commit"] = "keep"
    ingest_merge_idle_minutes: int = 60          # сборка после паузы в пачках
    ingest_merge_max_delay_minutes: int = 1440   # страховка: старейшая пачка старше
    ingest_merge_on_commit: bool = False         # 1С зовёт commit после каждой пачки
    ingest_version_coalesce_minutes: int = 240   # слияние версий разрезанного заказа
    ingest_batch_retention_days: int = 7
    # /commit в режиме direct: deferred — ответ сразу, агрегаты пересобирает worker
    # (раз в REFRESH_INTERVAL_MINUTES); sync — пересборка в запросе (прежнее поведение).
    # 1С зовёт commit после каждой пачки: sync замедлял выгрузку в разы.
    ingest_commit_refresh: Literal["sync", "deferred"] = "deferred"

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


settings = Settings()
