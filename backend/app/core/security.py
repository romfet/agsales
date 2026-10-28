"""App-to-app auth — token-request flow.

Standard process: a client POSTs its `client_id` + `client_secret` to
``POST /api/auth/token`` and gets a **server-signed** HS256 JWT carrying its
granted scopes. Protected endpoints accept **only** a valid issued token with the
required scope (``ingest`` for the 1С feed, ``analyze`` for the operator).

Dev posture: empty ``AUTH_JWT_SECRET`` = auth OPEN (local only). Production MUST
set the signing secret and register clients via ``AUTH_CLIENTS``.

PyJWT ≥ 2.13 rejects an empty HMAC key (``InvalidKeyError``) and ≥ 2.14 rejects keys
that look like public-key material; keys < 32 bytes only warn. With auth OPEN the
token endpoint still answers (signed with a per-process random key — protected
endpoints do not verify tokens in that mode), so a client that always requests a
token keeps working. ``check_signing_key`` validates the configured secret at startup.
"""
from __future__ import annotations

import logging
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import ClientCred, settings

_bearer = HTTPBearer(auto_error=False)
_ALGO = "HS256"
_MIN_KEY_BYTES = 32                       # RFC 7518 §3.2 для HS256
# Ключ подписи для режима «авторизация выключена»: токены формально валидны, но
# защищённые эндпоинты их в этом режиме не проверяют.
_OPEN_MODE_KEY = secrets.token_hex(32)

logger = logging.getLogger("auth")


def _signing_key() -> str:
    return settings.auth_jwt_secret or _OPEN_MODE_KEY


def check_signing_key() -> str:
    """Проверить AUTH_JWT_SECRET при старте: подписать и проверить пробный токен.

    Возвращает ``open`` (секрет пуст — авторизация выключена), ``ok``, ``weak``
    (короче 32 байт — PyJWT предупреждает) или ``invalid`` (PyJWT отвергает ключ —
    все защищённые эндпоинты будут отвечать 401)."""
    secret = settings.auth_jwt_secret
    if not secret:
        return "open"
    try:
        token = jwt.encode({"sub": "selfcheck"}, secret, algorithm=_ALGO)
        jwt.decode(token, secret, algorithms=[_ALGO])
    except jwt.PyJWTError as e:
        logger.error("AUTH_JWT_SECRET rejected by PyJWT (%s): every protected endpoint "
                     "will answer 401. Use a random secret: openssl rand -hex 32", e)
        return "invalid"
    if len(secret.encode()) < _MIN_KEY_BYTES:
        logger.warning("AUTH_JWT_SECRET is %d bytes, below the recommended %d for HS256. "
                       "Use: openssl rand -hex 32", len(secret.encode()), _MIN_KEY_BYTES)
        return "weak"
    return "ok"


def _clients() -> dict[str, ClientCred]:
    return {c.client_id: c for c in settings.auth_clients}


def issue_token(client_id: str, client_secret: str) -> dict:
    """Validate credentials and return a server-signed JWT + metadata."""
    client = _clients().get(client_id)
    if client is None or not secrets.compare_digest(client.client_secret, client_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    now = int(time.time())
    scope = " ".join(client.scopes)
    token = jwt.encode(
        {"sub": client_id, "scope": scope, "iat": now, "exp": now + settings.auth_token_ttl_seconds},
        _signing_key(),
        algorithm=_ALGO,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.auth_token_ttl_seconds,
        "scope": scope,
    }


def require_scope(scope: str):
    """Dependency factory: require a valid server-issued JWT carrying ``scope``."""

    async def _dep(
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        if not settings.auth_jwt_secret:
            return  # dev: open when no signing secret configured
        if creds is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = jwt.decode(creds.credentials, settings.auth_jwt_secret, algorithms=[_ALGO])
        except jwt.PyJWTError:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if scope not in set((payload.get("scope") or "").split()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"missing scope: {scope}")

    return _dep
