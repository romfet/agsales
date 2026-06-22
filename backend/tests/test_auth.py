"""Unit tests for the M2M auth (OAuth2 client credentials → JWT). No DB needed."""
import jwt
import pytest
from fastapi import HTTPException

from backend.app.core import security
from backend.app.core.config import ClientCred, settings


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "testsecret")
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    monkeypatch.setattr(
        settings,
        "auth_clients",
        [ClientCred(client_id="onec", client_secret="s3cr3t", scopes=["ingest", "analyze"])],
    )


def test_issue_token_ok(configured):
    tok = security.issue_token("onec", "s3cr3t")
    assert tok["token_type"] == "bearer"
    assert tok["expires_in"] == 3600
    payload = jwt.decode(tok["access_token"], "testsecret", algorithms=["HS256"])
    assert payload["sub"] == "onec"
    assert "ingest" in payload["scope"].split()


def test_issue_token_bad_secret(configured):
    with pytest.raises(HTTPException) as e:
        security.issue_token("onec", "wrong")
    assert e.value.status_code == 401


def test_issue_token_unknown_client(configured):
    with pytest.raises(HTTPException):
        security.issue_token("ghost", "whatever")


async def test_require_scope_open_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "")  # auth disabled
    dep = security.require_scope("ingest")
    assert await dep(None) is None


async def test_require_scope_accepts_valid_token(configured):
    from fastapi.security import HTTPAuthorizationCredentials

    tok = security.issue_token("onec", "s3cr3t")["access_token"]
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)
    dep = security.require_scope("ingest")
    assert await dep(creds) is None


async def test_require_scope_rejects_missing_scope(configured):
    from fastapi.security import HTTPAuthorizationCredentials

    monkeypatch_scopes = ClientCred(client_id="onec", client_secret="s3cr3t", scopes=["analyze"])
    settings.auth_clients = [monkeypatch_scopes]
    tok = security.issue_token("onec", "s3cr3t")["access_token"]
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)
    dep = security.require_scope("ingest")
    with pytest.raises(HTTPException) as e:
        await dep(creds)
    assert e.value.status_code == 403
