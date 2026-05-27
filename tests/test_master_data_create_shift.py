"""Tests for create_shift (POST /api/shifts) — the empty-name guard, the
duplicate-shift-name 409 business rule, and the sanitized-500 error handlers.
Driven with a fake db whose create_shift raises the relevant exception."""

import secrets

import api.routers.master_data as md
from starlette.testclient import TestClient


class _ShiftDB:
    def __init__(self, exc=None):
        self._exc = exc

    def create_shift(self, data):
        if self._exc:
            raise self._exc
        return {"ID": 1, "NAME": data.get("NAME")}


def _admin_session():
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 920, "NAME": "md_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    return tok


def _client(monkeypatch, db):
    from api.main import app

    monkeypatch.setattr(md, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


class TestCreateShift:
    _URL = "/api/shifts"

    def _post(self, client, tok, name="Frühschicht"):
        return client.post(self._URL, json={"NAME": name}, headers={"X-Auth-Token": tok})

    def test_blank_name_returns_400(self, monkeypatch):
        from api.main import _sessions

        tok = _admin_session()
        try:
            # whitespace passes min_length=1 but fails the explicit strip() guard
            resp = self._post(_client(monkeypatch, _ShiftDB()), tok, name="   ")
            assert resp.status_code == 400
        finally:
            _sessions.pop(tok, None)

    def test_duplicate_shift_name_returns_409(self, monkeypatch):
        from api.main import _sessions

        db = _ShiftDB(ValueError("DUPLICATE:SHIFTNAME:Frühschicht"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 409
            assert "existiert bereits" in resp.json()["detail"]
        finally:
            _sessions.pop(tok, None)

    def test_generic_value_error_is_sanitized_500(self, monkeypatch):
        from api.main import _sessions

        db = _ShiftDB(ValueError("some other problem"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "some other problem" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_unexpected_error_is_sanitized_500(self, monkeypatch):
        from api.main import _sessions

        db = _ShiftDB(RuntimeError("db boom"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "db boom" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_success_returns_record(self, monkeypatch):
        from api.main import _sessions

        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, _ShiftDB()), tok)
            assert resp.status_code == 200
            assert resp.json()["record"]["NAME"] == "Frühschicht"
        finally:
            _sessions.pop(tok, None)
