"""Tests for the self-service password-change endpoint
(POST /api/auth/change-password). The test fixtures carry no real user
passwords, so get_db is patched with a fake whose verify/change behaviour is
configurable, driving the verify → validate → change → invalidate flow."""

import secrets

from starlette.testclient import TestClient


class _PwDB:
    def __init__(self, *, verify=True, change=True):
        self._verify = verify
        self._change = change

    def verify_user_password(self, username, password):
        return {"ID": 500} if self._verify else None

    def change_password(self, user_id, new_password):
        return self._change


def _session(role="Admin"):
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 500,
        "NAME": "pwuser",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255,
        "_session_id": "sess-pw-self",
    }
    return tok


def _client(monkeypatch, db):
    from api.main import app
    from api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


class TestChangeOwnPassword:
    def _post(self, client, tok, old="OldPass1", new="NewPass123"):
        return client.post(
            "/api/auth/change-password",
            json={"old_password": old, "new_password": new},
            headers={"X-Auth-Token": tok},
        )

    def test_success(self, monkeypatch):
        from api.main import _sessions

        tok = _session()
        client = _client(monkeypatch, _PwDB(verify=True, change=True))
        try:
            resp = self._post(client, tok)
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert "sessions_revoked" in body
        finally:
            _sessions.pop(tok, None)

    def test_wrong_old_password_is_403(self, monkeypatch):
        from api.main import _sessions

        tok = _session()
        client = _client(monkeypatch, _PwDB(verify=False))
        try:
            resp = self._post(client, tok)
            assert resp.status_code == 403
        finally:
            _sessions.pop(tok, None)

    def test_weak_new_password_is_400(self, monkeypatch):
        from api.main import _sessions

        tok = _session()
        client = _client(monkeypatch, _PwDB(verify=True))
        try:
            # passes the schema (>=6 chars) but fails strength (no digit/upper)
            resp = self._post(client, tok, new="weakpass")
            assert resp.status_code == 400
        finally:
            _sessions.pop(tok, None)

    def test_change_password_user_not_found_is_404(self, monkeypatch):
        from api.main import _sessions

        tok = _session()
        client = _client(monkeypatch, _PwDB(verify=True, change=False))
        try:
            resp = self._post(client, tok)
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_db_error_is_sanitized_500(self, monkeypatch):
        from api.main import _sessions

        class _BoomDB(_PwDB):
            def change_password(self, user_id, new_password):
                raise RuntimeError("db boom")

        tok = _session()
        client = _client(monkeypatch, _BoomDB(verify=True))
        try:
            resp = self._post(client, tok)
            assert resp.status_code == 500
            assert "boom" not in resp.text  # raw error not leaked
        finally:
            _sessions.pop(tok, None)
