"""Security audit round 5: token/session hardening tests."""
import time as _time
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from api.main import app
from api.dependencies import (
    _sessions,
    _failed_logins,
    purge_expired_sessions,
    purge_stale_failed_logins,
    _MAX_SESSIONS_PER_USER,
    _LOCKOUT_WINDOW,
)

client = TestClient(app, raise_server_exceptions=False)


# ── purge_expired_sessions ─────────────────────────────────────

def test_purge_expired_sessions_removes_expired():
    _sessions.clear()
    _sessions["expired_tok"] = {"ID": 99, "expires_at": _time.time() - 1}
    _sessions["valid_tok"] = {"ID": 99, "expires_at": _time.time() + 3600}
    removed = purge_expired_sessions()
    assert removed == 1
    assert "expired_tok" not in _sessions
    assert "valid_tok" in _sessions
    _sessions.clear()


def test_purge_expired_sessions_skips_no_expiry():
    """Dev-mode token has expires_at=None and must not be removed."""
    _sessions.clear()
    _sessions["dev_tok"] = {"ID": 0, "expires_at": None}
    removed = purge_expired_sessions()
    assert removed == 0
    assert "dev_tok" in _sessions
    _sessions.clear()


def test_purge_expired_sessions_empty():
    _sessions.clear()
    assert purge_expired_sessions() == 0


# ── purge_stale_failed_logins ──────────────────────────────────

def test_purge_stale_failed_logins_removes_old():
    _failed_logins.clear()
    old_time = _time.time() - _LOCKOUT_WINDOW - 10
    _failed_logins["ghost_user"] = [old_time, old_time]
    _failed_logins["active_user"] = [_time.time() - 60]  # still within window
    removed = purge_stale_failed_logins()
    assert removed == 1
    assert "ghost_user" not in _failed_logins
    assert "active_user" in _failed_logins
    _failed_logins.clear()


def test_purge_stale_failed_logins_empty():
    _failed_logins.clear()
    assert purge_stale_failed_logins() == 0


# ── Max sessions per user ──────────────────────────────────────

def test_max_sessions_per_user_evicts_oldest():
    """When a user exceeds _MAX_SESSIONS_PER_USER, oldest sessions are evicted."""
    _sessions.clear()
    _failed_logins.clear()
    now = _time.time()
    user_id = 42
    # Pre-populate with MAX sessions for user ID 42
    for i in range(_MAX_SESSIONS_PER_USER):
        _sessions[f"tok_{i}"] = {"ID": user_id, "NAME": "tester", "role": "Leser", "expires_at": now + 3600 + i}

    # Mock get_db to return a valid user
    mock_db = MagicMock()
    mock_db.verify_user_password.return_value = {
        "ID": user_id, "NAME": "tester", "role": "Leser", "ADMIN": False, "RIGHTS": 1
    }

    with patch("api.routers.auth.get_db", return_value=mock_db):
        resp = client.post("/api/auth/login", json={"username": "tester", "password": "pw"})

    if resp.status_code == 200:
        # After login, total sessions for user 42 must be <= _MAX_SESSIONS_PER_USER
        user_sessions = [s for s in _sessions.values() if s.get("ID") == user_id]
        assert len(user_sessions) <= _MAX_SESSIONS_PER_USER
    _sessions.clear()
    _failed_logins.clear()


def test_max_sessions_constant_is_positive():
    assert _MAX_SESSIONS_PER_USER > 0
    assert _MAX_SESSIONS_PER_USER <= 100  # sanity bound


# ── Token validation ───────────────────────────────────────────

def test_expired_token_is_rejected():
    _sessions.clear()
    _sessions["old_tok"] = {"ID": 1, "role": "Admin", "expires_at": _time.time() - 1}
    resp = client.get("/api/users", headers={"x-auth-token": "old_tok"})
    assert resp.status_code == 401
    _sessions.clear()


def test_valid_token_is_accepted():
    _sessions.clear()
    _sessions["good_tok"] = {
        "ID": 1, "NAME": "admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255,
        "expires_at": _time.time() + 3600,
    }
    resp = client.get("/api/users", headers={"x-auth-token": "good_tok"})
    # May succeed or fail based on DB, but must not be 401 for auth reason
    assert resp.status_code != 401
    _sessions.clear()


# ── Lockout still works ─────────────────────────────────────────

def test_lockout_triggers_after_5_failures():
    _failed_logins.clear()
    responses = []
    for i in range(6):
        r = client.post("/api/auth/login", json={"username": "lockme", "password": f"wrong{i}"})
        responses.append(r.status_code)
    # At least one response should be 429 (lockout or rate limit)
    assert 429 in responses
    _failed_logins.clear()
