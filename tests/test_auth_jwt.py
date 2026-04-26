"""JWT mint/verify for dashboard auth (no database)."""

from __future__ import annotations

import uuid

import jwt
import pytest

from agent_hub_core.config.settings import Settings, get_settings

from services import auth_service


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    yield
    get_settings.cache_clear()


def test_create_decode_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret-at-least-32-chars-long")
    monkeypatch.setenv("JWT_ISSUER", "agent-hub-test")
    s = Settings()
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    token, ttl = auth_service.create_access_token(
        user_id=uid, tenant_id=tid, email="u@example.com", settings=s
    )
    assert ttl > 0
    payload = auth_service.decode_access_token(token, settings=s)
    assert payload["sub"] == str(uid)
    assert payload["tenant_id"] == str(tid)
    assert payload["email"] == "u@example.com"
    assert payload["iss"] == "agent-hub-test"


def test_decode_wrong_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("JWT_ISSUER", "agent-hub-test")
    s1 = Settings()
    token, _ = auth_service.create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="a@b.com",
        settings=s1,
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "fedcba9876543210fedcba9876543210")
    s2 = Settings()
    with pytest.raises(jwt.InvalidTokenError):
        auth_service.decode_access_token(token, settings=s2)


def test_decode_wrong_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret-at-least-32-chars-long")
    monkeypatch.setenv("JWT_ISSUER", "issuer-a")
    s1 = Settings()
    token, _ = auth_service.create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="a@b.com",
        settings=s1,
    )
    monkeypatch.setenv("JWT_ISSUER", "issuer-b")
    s2 = Settings()
    with pytest.raises(jwt.InvalidTokenError):
        auth_service.decode_access_token(token, settings=s2)
