"""API authentication dependency.

Placeholder bearer-token check, pending the customer's SSO/OIDC decision
(see docs/onec_contract.md open questions). Wired into every router now so the
auth seam exists; swap the body for OIDC validation when the mechanism is chosen.

If ``API_AUTH_TOKEN`` is unset the API is open — acceptable for local/dev only;
production deployments must set it until SSO is integrated.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import settings

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    token = settings.api_auth_token
    if not token:
        return  # dev posture: no token configured
    if credentials is None or credentials.credentials != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
