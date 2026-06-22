"""App-to-app (machine-to-machine) authorization — OAuth2 Client Credentials.

Callers obtain a short-lived bearer token from ``POST /api/auth/token`` with
their ``client_id`` / ``client_secret``, then send it as
``Authorization: Bearer <token>`` on protected endpoints. Tokens are HS256 JWTs
carrying the client's granted scopes; each endpoint requires a scope
(``ingest`` for the 1С feed, ``analyze`` for the operator).

Dev posture: if ``AUTH_JWT_SECRET`` is unset the API is OPEN (local/mock only).
Production MUST set the secret and register clients via ``AUTH_CLIENTS``.
"""
from __future__ import annotations

import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import settings

_bearer = HTTPBearer(auto_error=False)
_ALGO = "HS256"


def auth_enabled() -> bool:
    return bool(settings.auth_jwt_secret)


def _clients() -> dict[str, "object"]:
    return {c.client_id: c for c in settings.auth_clients}


def issue_token(client_id: str, client_secret: str) -> dict:
    """Validate client credentials and mint a scoped JWT (client-credentials grant)."""
    client = _clients().get(client_id)
    if client is None or not secrets.compare_digest(client.client_secret, client_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    now = int(time.time())
    scope = " ".join(client.scopes)
    payload = {
        "sub": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + settings.auth_token_ttl_seconds,
    }
    token = jwt.encode(payload, settings.auth_jwt_secret, algorithm=_ALGO)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.auth_token_ttl_seconds,
        "scope": scope,
    }


def require_scope(scope: str):
    """Dependency factory: require a valid bearer JWT carrying ``scope``."""

    async def _dep(
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        if not auth_enabled():
            return  # dev: open when no secret configured
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
        granted = set((payload.get("scope") or "").split())
        if scope not in granted:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"missing scope: {scope}")

    return _dep
