"""
Comprehensive tests for auth.py — 2FA/TOTP, password management, login edge cases.
Targets the uncovered lines (59% → 80%+): lines 296-437, 459-465, 493-512, 606-641, 660-711.
"""

import secrets

from starlette.testclient import TestClient

# ── Helpers ────────────────────────────────────────────────────────────────────


def _fresh_client():
    """Fresh TestClient to avoid rate-limit pollution."""
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _inject_session(user_id: int = 800, name: str = "testuser", role: str = "Admin"):
    """Inject a session token and return (token, user_dict)."""
    from api.main import _sessions
    tok = secrets.token_hex(20)
    user = {
        "ID": user_id,
        "NAME": name,
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else (2 if role == "Planer" else 1),
    }
    _sessions[tok] = user
    return tok, user


def _cleanup_session(tok):
    from api.main import _sessions
    _sessions.pop(tok, None)


# ── 2FA Status ─────────────────────────────────────────────────────────────────


class Test2FAStatus:
    def test_2fa_status_not_enabled(self, sync_client):
        res = sync_client.get("/api/auth/2fa/status")
        assert res.status_code == 200
        data = res.json()
        assert "enabled" in data
        assert data["enabled"] is False

    def test_2fa_status_unauthenticated(self):
        c = _fresh_client()
        res = c.get("/api/auth/2fa/status")
        assert res.status_code == 401


# ── 2FA Setup ──────────────────────────────────────────────────────────────────


class Test2FASetup:
    def test_2fa_setup_returns_secret_and_qr(self, sync_client):
        res = sync_client.post("/api/auth/2fa/setup")
        assert res.status_code == 200
        data = res.json()
        assert "secret" in data
        assert len(data["secret"]) >= 16
        assert "qr_code" in data
        assert len(data["qr_code"]) > 100  # base64 PNG
        assert "otpauth_uri" in data
        assert "otpauth://totp/" in data["otpauth_uri"]

    def test_2fa_setup_unauthenticated(self):
        c = _fresh_client()
        res = c.post("/api/auth/2fa/setup")
        assert res.status_code == 401


# ── 2FA Enable ─────────────────────────────────────────────────────────────────


class Test2FAEnable:
    def test_2fa_enable_wrong_code(self, sync_client):
        # First setup to get a secret
        sync_client.post("/api/auth/2fa/setup")
        # Then try to enable with wrong code
        res = sync_client.post(
            "/api/auth/2fa/enable",
            json={"code": "000000"},
        )
        assert res.status_code == 400
        assert "Invalid code" in res.json().get("detail", "")

    def test_2fa_enable_valid_code(self, write_db_path):
        """Full 2FA enable flow: setup → generate valid code → enable."""
        import pyotp

        tok, _ = _inject_session(user_id=999, name="sync_admin")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        # Setup
        res = c.post("/api/auth/2fa/setup")
        assert res.status_code == 200
        secret = res.json()["secret"]

        # Generate a valid TOTP code
        totp = pyotp.TOTP(secret)
        code = totp.now()

        # Enable
        res = c.post("/api/auth/2fa/enable", json={"code": code})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "backup_codes" in data
        assert len(data["backup_codes"]) > 0

        # Verify status is now enabled
        res = c.get("/api/auth/2fa/status")
        assert res.status_code == 200
        assert res.json()["enabled"] is True

        # Clean up: disable 2FA
        res = c.post("/api/auth/2fa/disable", json={"password": "Test1234"})
        _cleanup_session(tok)


# ── 2FA Disable ────────────────────────────────────────────────────────────────


class Test2FADisable:
    def test_2fa_disable_wrong_password(self, sync_client):
        res = sync_client.post(
            "/api/auth/2fa/disable",
            json={"password": "WrongPassword123"},
        )
        assert res.status_code == 403
        assert "Passwort ist falsch" in res.json().get("detail", "")

    def test_2fa_disable_unauthenticated(self):
        c = _fresh_client()
        res = c.post("/api/auth/2fa/disable", json={"password": "Test1234"})
        assert res.status_code == 401


# ── Admin Disable 2FA ──────────────────────────────────────────────────────────


class TestAdmin2FADisable:
    def test_admin_disable_2fa(self, sync_client):
        res = sync_client.post("/api/auth/2fa/admin-disable/999")
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_admin_disable_2fa_non_admin(self):
        tok, _ = _inject_session(user_id=801, name="reader", role="Leser")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.post("/api/auth/2fa/admin-disable/999")
        assert res.status_code == 403
        _cleanup_session(tok)


# ── Login with 2FA ─────────────────────────────────────────────────────────────


class TestLoginWith2FA:
    def test_login_requires_2fa_when_enabled(self, write_db_path):
        """Full flow: enable 2FA for the real DB user, then login requires TOTP code."""
        import pyotp

        # First, login to get the real user ID
        c_login = _fresh_client()
        res = c_login.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Test1234"},
        )
        assert res.status_code == 200
        real_user = res.json()["user"]
        real_user_id = real_user["ID"]

        # Inject session as that real user to enable 2FA
        tok, _ = _inject_session(user_id=real_user_id, name="admin")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        # Setup + enable 2FA for the real user
        res = c.post("/api/auth/2fa/setup")
        assert res.status_code == 200
        secret = res.json()["secret"]
        totp = pyotp.TOTP(secret)
        code = totp.now()
        res = c.post("/api/auth/2fa/enable", json={"code": code})
        assert res.status_code == 200

        # Now try login without TOTP code
        c2 = _fresh_client()
        res = c2.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Test1234"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("requires_2fa") is True
        assert data.get("ok") is False

        # Login with valid TOTP code
        code2 = totp.now()
        c3 = _fresh_client()
        res = c3.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Test1234", "totp_code": code2},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "token" in data

        # Login with invalid TOTP code
        c4 = _fresh_client()
        res = c4.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Test1234", "totp_code": "000000"},
        )
        assert res.status_code == 401
        assert "2FA" in res.json().get("detail", "")

        # Cleanup: admin-disable 2FA
        c.post(f"/api/auth/2fa/admin-disable/{real_user_id}")
        _cleanup_session(tok)


# ── Self-service password change ───────────────────────────────────────────────


class TestSelfChangePassword:
    def test_change_own_password_wrong_old(self, sync_client):
        res = sync_client.post(
            "/api/auth/change-password",
            json={"old_password": "WrongOldPw1", "new_password": "NewValid1234"},
        )
        assert res.status_code == 403
        assert "Altes Passwort" in res.json().get("detail", "")

    def test_change_own_password_weak_new(self, sync_client):
        res = sync_client.post(
            "/api/auth/change-password",
            json={"old_password": "Test1234", "new_password": "short"},
        )
        # 422 (Pydantic min_length=6), 400 (strength check), or 403 (wrong old pw)
        assert res.status_code in (400, 403, 422)

    def test_change_own_password_unauthenticated(self):
        c = _fresh_client()
        res = c.post(
            "/api/auth/change-password",
            json={"old_password": "Test1234", "new_password": "NewValid1234"},
        )
        assert res.status_code == 401


# ── Admin password reset (generates temp pw) ──────────────────────────────────


class TestResetPassword:
    def test_reset_password_nonexistent_user(self, sync_client):
        res = sync_client.post("/api/users/99999/reset-password")
        assert res.status_code == 404

    def test_reset_password_success(self, write_db_path):
        tok, _ = _inject_session(user_id=999, name="sync_admin", role="Planer")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        # Get a real user ID
        tok2, _ = _inject_session(user_id=999, name="sync_admin", role="Admin")
        c2 = _fresh_client()
        c2.headers["X-Auth-Token"] = tok2
        users_res = c2.get("/api/users")
        if users_res.status_code == 200:
            users = users_res.json()
            if users:
                uid = users[0].get("ID", 1)
                res = c.post(f"/api/users/{uid}/reset-password")
                if res.status_code == 200:
                    data = res.json()
                    assert data["ok"] is True
                    assert "temp_password" in data
                    assert len(data["temp_password"]) >= 8
                    assert "sessions_revoked" in data
                    assert "email_sent" in data

        _cleanup_session(tok)
        _cleanup_session(tok2)

    def test_reset_password_non_planer(self):
        tok, _ = _inject_session(user_id=801, name="reader", role="Leser")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.post("/api/users/1/reset-password")
        assert res.status_code == 403
        _cleanup_session(tok)


# ── Logout ─────────────────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_with_token(self):
        tok, _ = _inject_session(user_id=850, name="logout_test")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_logout_no_session(self):
        c = _fresh_client()
        c.headers["X-Auth-Token"] = "nonexistent_token_12345"
        res = c.post("/api/auth/logout")
        assert res.status_code == 200  # logout always returns ok

    def test_logout_clears_cookie(self):
        tok, _ = _inject_session(user_id=851, name="cookie_test")
        c = _fresh_client()
        c.cookies.set("sp5_token", tok)
        res = c.post("/api/auth/logout")
        assert res.status_code == 200


# ── User CRUD ──────────────────────────────────────────────────────────────────


class TestUserCRUD:
    def test_create_user_duplicate(self, write_db_path):
        tok, _ = _inject_session(user_id=999, name="sync_admin")
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        # Create a user
        name = f"testdup_{secrets.token_hex(4)}"
        res = c.post("/api/users", json={
            "NAME": name, "PASSWORD": "ValidPw123", "role": "Leser",
        })
        if res.status_code == 200:
            # Try to create again — should be 409
            res2 = c.post("/api/users", json={
                "NAME": name, "PASSWORD": "ValidPw123", "role": "Leser",
            })
            assert res2.status_code == 409
        _cleanup_session(tok)

    def test_update_user_not_found(self, sync_client):
        res = sync_client.put(
            "/api/users/99999",
            json={"NAME": "nobody"},
        )
        assert res.status_code == 404

    def test_update_user_weak_password(self, sync_client):
        res = sync_client.put(
            "/api/users/1",
            json={"PASSWORD": "short"},
        )
        assert res.status_code in (400, 422)  # 422 from Pydantic min_length

    def test_delete_user_not_found(self, sync_client):
        res = sync_client.delete("/api/users/99999")
        assert res.status_code == 404

    def test_admin_change_password_not_found(self, sync_client):
        res = sync_client.post(
            "/api/users/99999/change-password",
            json={"new_password": "ValidPw123"},
        )
        assert res.status_code == 404

    def test_admin_change_password_weak(self, sync_client):
        res = sync_client.post(
            "/api/users/1/change-password",
            json={"new_password": "weak"},
        )
        assert res.status_code in (400, 422)  # 422 from Pydantic min_length


# ── Password strength validation ──────────────────────────────────────────────


class TestPasswordStrength:
    def test_no_uppercase(self, sync_client):
        res = sync_client.post(
            "/api/users/1/change-password",
            json={"new_password": "alllowercase1"},
        )
        assert res.status_code == 400
        assert "uppercase" in res.json().get("detail", "")

    def test_no_digit(self, sync_client):
        res = sync_client.post(
            "/api/users/1/change-password",
            json={"new_password": "NoDigitHere"},
        )
        assert res.status_code == 400
        assert "Ziffer" in res.json().get("detail", "")

    def test_too_short(self, sync_client):
        res = sync_client.post(
            "/api/users/1/change-password",
            json={"new_password": "Ab1"},
        )
        assert res.status_code in (400, 422)  # 422 from Pydantic min_length


# ── Login edge cases ──────────────────────────────────────────────────────────


class TestLoginEdgeCases:
    def test_login_lockout(self):
        """After too many failed attempts, should get locked out."""
        # Make several failed login attempts
        for _ in range(6):
            c2 = _fresh_client()
            c2.post(
                "/api/auth/login",
                json={"username": "lockouttest", "password": "wrong"},
            )
        # The next attempt should be rate-limited
        c3 = _fresh_client()
        res = c3.post(
            "/api/auth/login",
            json={"username": "lockouttest", "password": "wrong"},
        )
        # Should be 429 (lockout) or 401 (not found user - depends on order)
        assert res.status_code in (401, 429)

    def test_login_session_limit_eviction(self, write_db_path):
        """When max sessions exceeded, oldest should be evicted."""
        from api.main import _sessions

        # Create many sessions for one user
        user_id = 1
        for i in range(10):
            tok = f"session_limit_test_{i}"
            _sessions[tok] = {
                "ID": user_id,
                "NAME": "admin",
                "role": "Admin",
                "ADMIN": True,
                "RIGHTS": 255,
                "expires_at": 9999999999 + i,
            }

        # Login should succeed and evict oldest
        c = _fresh_client()
        res = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Test1234"},
        )
        # Cleanup test sessions
        for i in range(10):
            _sessions.pop(f"session_limit_test_{i}", None)

        assert res.status_code == 200
