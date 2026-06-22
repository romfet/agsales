"""Unit tests for the token-request auth (POST /auth/token → scoped JWT). No DB."""
import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.app.core import security
from backend.app.core.config import ClientCred, settings


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "server-key")
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    monkeypatch.setattr(
        settings,
        "auth_clients",
        [ClientCred(client_id="onec", client_secret="s3cr3t", scopes=["ingest"])],
    )


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_issue_token_ok(configured):
    tok = security.issue_token("onec", "s3cr3t")
    assert tok["token_type"] == "bearer" and "ingest" in tok["scope"].split()
    payload = jwt.decode(tok["access_token"], "server-key", algorithms=["HS256"])
    assert payload["sub"] == "onec"


def test_issue_token_bad_secret(configured):
    with pytest.raises(HTTPException) as e:
        security.issue_token("onec", "wrong")
    assert e.value.status_code == 401


async def test_require_scope_open_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "")
    assert await security.require_scope("ingest")(None) is None


async def test_require_scope_accepts_valid(configured):
    tok = security.issue_token("onec", "s3cr3t")["access_token"]
    assert await security.require_scope("ingest")(_creds(tok)) is None


async def test_require_scope_rejects_other_scope(configured):
    tok = security.issue_token("onec", "s3cr3t")["access_token"]  # scope=ingest
    with pytest.raises(HTTPException) as e:
        await security.require_scope("analyze")(_creds(tok))
    assert e.value.status_code == 403


async def test_require_scope_rejects_missing(configured):
    with pytest.raises(HTTPException) as e:
        await security.require_scope("ingest")(None)
    assert e.value.status_code == 401
