"""OAuth2 Client Credentials token endpoint (machine-to-machine auth)."""
from fastapi import APIRouter, Form, HTTPException, status

from backend.app.core.security import issue_token
from backend.app.schemas import TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenOut)
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    if grant_type != "client_credentials":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unsupported_grant_type")
    return issue_token(client_id, client_secret)
