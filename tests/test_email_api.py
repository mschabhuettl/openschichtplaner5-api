"""Tests for the /api/email/* endpoints."""

import secrets
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def _inject_token(role: str) -> str:
    from api.main import _sessions

    tok = secrets.token_hex(16)
    _sessions[tok] = {
        "ID": 990,
        "NAME": f"test_{role.lower()}",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 0,
    }
    return tok


def _remove_token(tok: str) -> None:
    from api.main import _sessions
    _sessions.pop(tok, None)


def _h(token: str) -> dict:
    return {"X-Auth-Token": token}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_token():
    tok = _inject_token("Admin")
    yield tok
    _remove_token(tok)


@pytest.fixture
def planer_token():
    tok = _inject_token("Planer")
    yield tok
    _remove_token(tok)


@pytest.fixture
def sync_client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── GET /api/email/config ─────────────────────────────────────────────────────


class TestGetEmailConfig:
    def test_returns_config(self, sync_client, admin_token) -> None:
        resp = sync_client.get("/api/email/config", headers=_h(admin_token))
        assert resp.status_code == 200
        data = resp.json()
        assert "host" in data
        assert "enabled" in data
        assert "is_configured" in data
        assert "password" not in data

    def test_requires_admin(self, sync_client, planer_token) -> None:
        resp = sync_client.get("/api/email/config", headers=_h(planer_token))
        assert resp.status_code == 403

    def test_requires_auth(self, sync_client) -> None:
        resp = sync_client.get("/api/email/config")
        assert resp.status_code == 401


# ── POST /api/email/test ──────────────────────────────────────────────────────


class TestSendTestEmail:
    def test_not_configured_returns_400(
        self, sync_client, admin_token, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        resp = sync_client.post(
            "/api/email/test",
            headers=_h(admin_token),
            json={"to": "test@example.com"},
        )
        assert resp.status_code == 400
        assert "nicht konfiguriert" in resp.json()["detail"]

    @patch("sp5lib.email_service.send_email", return_value=True)
    def test_success(
        self,
        mock_send: MagicMock,
        sync_client,
        admin_token,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_USER", "noreply@test.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        resp = sync_client.post(
            "/api/email/test",
            headers=_h(admin_token),
            json={"to": "test@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("sp5lib.email_service.send_email", return_value=False)
    def test_failure(
        self,
        mock_send: MagicMock,
        sync_client,
        admin_token,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_USER", "noreply@test.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        resp = sync_client.post(
            "/api/email/test",
            headers=_h(admin_token),
            json={"to": "test@example.com"},
        )
        assert resp.status_code == 500

    def test_requires_admin(self, sync_client, planer_token) -> None:
        resp = sync_client.post(
            "/api/email/test",
            headers=_h(planer_token),
            json={"to": "test@example.com"},
        )
        assert resp.status_code == 403

    def test_requires_auth(self, sync_client) -> None:
        resp = sync_client.post(
            "/api/email/test",
            json={"to": "test@example.com"},
        )
        assert resp.status_code == 401

    def test_validation_missing_to(self, sync_client, admin_token) -> None:
        resp = sync_client.post(
            "/api/email/test",
            headers=_h(admin_token),
            json={},
        )
        assert resp.status_code == 422
