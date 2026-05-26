"""Tests for the admin password-reset email path
(POST /api/users/{id}/reset-password). The existing reset tests never reach
the email-sending block because the fixtures have no configured SMTP + a
name-matching employee email, so it's driven here with fakes."""

import secrets
from unittest.mock import MagicMock

from starlette.testclient import TestClient


class _ResetDB:
    def __init__(self, employees):
        self._employees = employees

    def change_password(self, user_id, new_password):
        return True

    def get_users(self):
        return [{"ID": 7, "NAME": "Max Müller"}]

    def get_employees(self):
        return self._employees


def _planer_session():
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 900,
        "NAME": "reset_admin",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
        "_session_id": "sess-reset",
    }
    return tok


def _setup(monkeypatch, employees, configured=True):
    from api.routers import auth as auth_router
    from sp5lib import email_service

    monkeypatch.setattr(auth_router, "get_db", lambda: _ResetDB(employees))
    monkeypatch.setattr(
        email_service,
        "get_config",
        lambda: MagicMock(is_configured=configured, app_url="http://app.test"),
    )
    sent = MagicMock()
    monkeypatch.setattr(email_service, "send_email_async", sent)
    return sent


class TestResetPasswordEmail:
    def test_sends_email_when_configured_and_employee_matches(self, monkeypatch):
        from api.main import _sessions, app

        sent = _setup(
            monkeypatch,
            [{"ID": 7, "NAME": "Müller", "FIRSTNAME": "Max", "EMAIL": "max@firma.de"}],
        )
        tok = _planer_session()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok
            r = c.post("/api/users/7/reset-password")
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["temp_password"]
            assert body["email_sent"] is True
            sent.assert_called_once()
            assert sent.call_args.kwargs["to"] == "max@firma.de"
        finally:
            _sessions.pop(tok, None)

    def test_email_send_failure_is_swallowed(self, monkeypatch):
        from api.main import _sessions, app

        sent = _setup(
            monkeypatch,
            [{"ID": 7, "NAME": "Müller", "FIRSTNAME": "Max", "EMAIL": "max@firma.de"}],
        )
        sent.side_effect = RuntimeError("smtp down")
        tok = _planer_session()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok
            r = c.post("/api/users/7/reset-password")
            # reset still succeeds; the email failure is swallowed and logged
            assert r.status_code == 200
            assert r.json()["email_sent"] is False
        finally:
            _sessions.pop(tok, None)

    def test_no_email_when_no_matching_employee(self, monkeypatch):
        from api.main import _sessions, app

        sent = _setup(
            monkeypatch,
            [{"ID": 8, "NAME": "Andere", "FIRSTNAME": "Wer", "EMAIL": "x@y.de"}],
        )
        tok = _planer_session()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok
            r = c.post("/api/users/7/reset-password")
            assert r.status_code == 200
            assert r.json()["email_sent"] is False
            sent.assert_not_called()
        finally:
            _sessions.pop(tok, None)
