"""Token endpoint — client posts its secret, gets a server-signed JWT."""
from fastapi import APIRouter

from backend.app.core.security import issue_token
from backend.app.schemas import TokenOut, TokenRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenOut)
async def token(body: TokenRequest):
    return issue_token(body.client_id, body.client_secret)
