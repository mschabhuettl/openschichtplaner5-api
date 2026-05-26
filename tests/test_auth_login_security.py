"""Tests for the login endpoint's security branches: brute-force lockout,
2FA enforcement and the per-user concurrent-session limit. Driven with a
fake db and direct manipulation of the in-memory lockout / session stores."""

import time
from unittest.mock import patch

import api.routers.auth as auth
from starlette.testclient import TestClient

_LOGIN = "/api/auth/login"


class _LoginDB:
    def __init__(self, *, user=None, totp_enabled=False, totp_ok=True):
        self._user = user
        self._totp_enabled = totp_enabled
        self._totp_ok = totp_ok

    def verify_user_password(self, username, password):
        return self._user

    def totp_get_status(self, user_id):
        return self._totp_enabled

    def totp_verify(self, user_id, code):
        return self._totp_ok


def _client():
    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_brute_force_lockout_returns_429():
    username = "lockme_sec"
    auth._failed_logins[username] = [time.time()] * auth._LOCKOUT_MAX
    try:
        resp = _client().post(_LOGIN, json={"username": username, "password": "whatever"})
        assert resp.status_code == 429
    finally:
        auth._failed_logins.pop(username, None)


def test_2fa_required_without_code():
    with patch.object(
        auth, "get_db", lambda: _LoginDB(user={"ID": 1, "NAME": "u"}, totp_enabled=True)
    ):
        resp = _client().post(_LOGIN, json={"username": "u", "password": "pw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["requires_2fa"] is True
    assert body["ok"] is False


def test_2fa_invalid_code_returns_401():
    db = _LoginDB(user={"ID": 1, "NAME": "u"}, totp_enabled=True, totp_ok=False)
    username = "twofa_bad"
    with patch.object(auth, "get_db", lambda: db):
        resp = _client().post(
            _LOGIN, json={"username": username, "password": "pw", "totp_code": "000000"}
        )
    try:
        assert resp.status_code == 401
    finally:
        auth._failed_logins.pop(username, None)


def test_session_limit_evicts_oldest():
    user = {"ID": 4242, "NAME": "sessuser", "role": "Admin"}
    db = _LoginDB(user=user, totp_enabled=False)
    seeded = []
    for i in range(auth._MAX_SESSIONS_PER_USER):
        tok = f"seed-sess-{i}"
        auth._sessions[tok] = {"ID": 4242, "expires_at": 1000 + i, "_session_id": tok}
        seeded.append(tok)
    try:
        with patch.object(auth, "get_db", lambda: db):
            resp = _client().post(_LOGIN, json={"username": "sessuser", "password": "pw"})
        assert resp.status_code == 200
        # eviction keeps the per-user session count at or below the limit
        count = sum(1 for s in auth._sessions.values() if s.get("ID") == 4242)
        assert count <= auth._MAX_SESSIONS_PER_USER
    finally:
        for tok in list(auth._sessions):
            if auth._sessions[tok].get("ID") == 4242:
                auth._sessions.pop(tok, None)
        auth._failed_logins.pop("sessuser", None)
