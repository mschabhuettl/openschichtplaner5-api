"""Unit tests for the auth/security helpers in api/dependencies.py — JWT
decoding, token validity (incl. server-side revocation/expiry), session
resolution, the role guards, session invalidation and audit-log resilience."""

import time
from unittest.mock import MagicMock, patch

import api.dependencies as deps
import pytest
from fastapi import HTTPException


def _future() -> float:
    return time.time() + 3600


def _past() -> float:
    return time.time() - 10


@pytest.fixture(autouse=True)
def _restore_sessions():
    """Snapshot and restore the in-memory session store around each test."""
    before = dict(deps._sessions)
    yield
    deps._sessions.clear()
    deps._sessions.update(before)


class TestDecodeJwt:
    def test_expired_token_returns_none(self):
        tok = deps.create_jwt_token({"ID": 1, "role": "Admin"}, _past())
        assert deps._decode_jwt(tok) is None

    def test_garbage_token_returns_none(self):
        assert deps._decode_jwt("not.a.jwt") is None


class TestIsTokenValid:
    def test_jwt_with_expired_session_is_invalid_and_purged(self):
        tok = deps.create_jwt_token({"ID": 2, "role": "Planer"}, _future())
        sid = deps._decode_jwt(tok)["sid"]
        deps._sessions[sid]["expires_at"] = _past()  # server-side expiry
        assert deps._is_token_valid(tok) is False
        assert sid not in deps._sessions  # purged

    def test_revoked_jwt_is_invalid(self):
        tok = deps.create_jwt_token({"ID": 3, "role": "Admin"}, _future())
        sid = deps._decode_jwt(tok)["sid"]
        del deps._sessions[sid]  # revoke server-side
        assert deps._is_token_valid(tok) is False

    def test_valid_jwt_is_valid(self):
        tok = deps.create_jwt_token({"ID": 4, "role": "Admin"}, _future())
        assert deps._is_token_valid(tok) is True


class TestGetSessionFromToken:
    def test_unknown_token_returns_none(self):
        assert deps._get_session_from_token("not.a.jwt") is None

    def test_jwt_without_sid_returns_none(self):
        # A validly-signed JWT that carries no server-side session id.
        tok = deps._jwt.encode({"uid": 9}, deps._JWT_SECRET, algorithm=deps._JWT_ALGORITHM)
        assert deps._get_session_from_token(tok) is None


class TestGetCurrentUser:
    def _req(self, cookies=None, query=None):
        r = MagicMock()
        r.cookies = cookies or {}
        r.query_params = query or {}
        return r

    def test_no_token_returns_none(self):
        assert deps.get_current_user(self._req(), None) is None

    def test_invalid_token_returns_none(self):
        assert deps.get_current_user(self._req(), "garbage.token.value") is None

    def test_header_token_resolves_session(self):
        tok = deps.create_jwt_token({"ID": 5, "role": "Admin", "NAME": "X"}, _future())
        user = deps.get_current_user(self._req(), tok)
        assert user is not None and user.get("ID") == 5

    def test_cookie_token_resolves_session(self):
        tok = deps.create_jwt_token({"ID": 6, "role": "Admin"}, _future())
        user = deps.get_current_user(self._req(cookies={"sp5_token": tok}), None)
        assert user is not None and user.get("ID") == 6


class TestRoleGuards:
    def test_require_auth_rejects_anonymous(self):
        with pytest.raises(HTTPException) as exc:
            deps.require_auth(None)
        assert exc.value.status_code == 401

    def test_require_role_factory(self):
        dep = deps.require_role("Admin")
        with pytest.raises(HTTPException) as e_anon:
            dep(None)
        assert e_anon.value.status_code == 401
        with pytest.raises(HTTPException) as e_low:
            dep({"role": "Leser"})
        assert e_low.value.status_code == 403
        assert dep({"role": "Admin"})["role"] == "Admin"

    def test_require_admin(self):
        with pytest.raises(HTTPException) as e_anon:
            deps.require_admin(None)
        assert e_anon.value.status_code == 401
        with pytest.raises(HTTPException) as e_low:
            deps.require_admin({"role": "Planer"})
        assert e_low.value.status_code == 403
        assert deps.require_admin({"role": "Admin"})["role"] == "Admin"

    def test_require_planer_rejects_anonymous(self):
        with pytest.raises(HTTPException) as exc:
            deps.require_planer(None)
        assert exc.value.status_code == 401

    def test_require_planer_rejects_leser(self):
        with pytest.raises(HTTPException) as exc:
            deps.require_planer({"role": "Leser"})
        assert exc.value.status_code == 403
        assert deps.require_planer({"role": "Planer"})["role"] == "Planer"


class TestMiscHelpers:
    def test_text_formatter_appends_traceback(self):
        import logging
        import sys

        fmt = deps._TextFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            rec = logging.LogRecord("n", logging.ERROR, __file__, 1, "msg", None, sys.exc_info())
        out = fmt.format(rec)
        assert "ValueError" in out and "boom" in out

    def test_get_db_uses_postgres_backend_when_configured(self):
        with (
            patch("sp5lib.db_config.is_postgresql", return_value=True),
            patch("sp5lib.db_factory.get_database", return_value="PG_DB") as get_database,
        ):
            assert deps.get_db() == "PG_DB"
        get_database.assert_called_once()


class TestSessionInvalidation:
    def test_invalidate_sessions_for_user_removes_matching(self):
        deps._sessions["s1"] = {"ID": 77, "_session_id": "s1"}
        deps._sessions["s2"] = {"ID": 77, "_session_id": "s2"}
        deps._sessions["s3"] = {"ID": 88, "_session_id": "s3"}
        removed = deps.invalidate_sessions_for_user(77)
        assert removed == 2
        assert "s1" not in deps._sessions
        assert "s2" not in deps._sessions
        assert "s3" in deps._sessions  # different user untouched

    def test_invalidate_keeps_excepted_session(self):
        deps._sessions["k1"] = {"ID": 90, "_session_id": "k1"}
        deps._sessions["k2"] = {"ID": 90, "_session_id": "k2"}
        removed = deps.invalidate_sessions_for_user(90, except_session_id="k1")
        assert removed == 1
        assert "k1" in deps._sessions
        assert "k2" not in deps._sessions


class TestAuditLog:
    def test_write_audit_log_swallows_errors(self, tmp_path):
        # Parent of the audit path is a regular file → open fails, swallowed.
        notadir = tmp_path / "afile"
        notadir.write_text("x", encoding="utf-8")
        bad = str(notadir / "audit.log")
        with patch.object(deps, "_AUDIT_LOG_FILE", bad):
            deps.write_audit_log("TEST_ACTION", "tester", {"detail": "value"})  # no raise
