"""App-to-app auth — token-request flow.

Standard process: a client POSTs its `client_id` + `client_secret` to
``POST /api/auth/token`` and gets a **server-signed** HS256 JWT carrying its
granted scopes. Protected endpoints accept **only** a valid issued token with the
required scope (``ingest`` for the 1С feed, ``analyze`` for the operator).

Dev posture: empty ``AUTH_JWT_SECRET`` = auth OPEN (local only). Production MUST
set the signing secret and register clients via ``AUTH_CLIENTS``.
"""
from __future__ import annotations

import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import ClientCred, settings

_bearer = HTTPBearer(auto_error=False)
_ALGO = "HS256"


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
        settings.auth_jwt_secret,
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
