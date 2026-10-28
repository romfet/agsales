"""Unit tests for the token-request auth (POST /auth/token → scoped JWT). No DB."""
import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.app.core import security
from backend.app.core.config import ClientCred, settings


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "server-key-" + "0" * 32)
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
    payload = jwt.decode(tok["access_token"], "server-key-" + "0" * 32, algorithms=["HS256"])
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


# --- PyJWT 2.14: пустой ключ запрещён, ключ проверяется на «публичный» материал ---

def test_token_endpoint_works_when_auth_open(monkeypatch):
    """AUTH_JWT_SECRET пуст (авторизация выключена): PyJWT ≥ 2.13 не подписывает пустым
    ключом. /api/auth/token не должен падать 500 — клиент (1С) всегда сначала берёт токен."""
    monkeypatch.setattr(settings, "auth_jwt_secret", "")
    monkeypatch.setattr(settings, "auth_clients",
                        [ClientCred(client_id="onec", client_secret="s3cr3t", scopes=["ingest"])])
    tok = security.issue_token("onec", "s3cr3t")
    assert tok["token_type"] == "bearer" and tok["access_token"].count(".") == 2


async def test_open_mode_token_is_accepted_by_protected_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "auth_jwt_secret", "")
    monkeypatch.setattr(settings, "auth_clients",
                        [ClientCred(client_id="onec", client_secret="s3cr3t", scopes=["ingest"])])
    tok = security.issue_token("onec", "s3cr3t")["access_token"]
    assert await security.require_scope("ingest")(_creds(tok)) is None


@pytest.mark.parametrize("secret, expected", [
    ("", "open"),
    ("server-key", "weak"),
    ("0f" * 32, "ok"),
    ("-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----", "invalid"),
    ('{"kty":"RSA","n":"abc","e":"AQAB"}', "invalid"),
])
def test_check_signing_key(monkeypatch, secret, expected):
    monkeypatch.setattr(settings, "auth_jwt_secret", secret)
    assert security.check_signing_key() == expected


async def test_require_scope_rejects_expired_foreign_and_unsigned(configured, monkeypatch):
    import time as _t
    expired = jwt.encode({"sub": "onec", "scope": "ingest", "exp": int(_t.time()) - 10},
                         "server-key-" + "0" * 32, algorithm="HS256")
    foreign = jwt.encode({"sub": "onec", "scope": "ingest"}, "0f" * 32, algorithm="HS256")
    unsigned = jwt.encode({"sub": "onec", "scope": "ingest"}, None, algorithm="none")
    for bad in (expired, foreign, unsigned, "not-a-jwt"):
        with pytest.raises(HTTPException) as e:
            await security.require_scope("ingest")(_creds(bad))
        assert e.value.status_code == 401, bad
