"""Tests for create_shift (POST /api/shifts) — the empty-name guard, the
duplicate-shift-name 409 business rule, and the sanitized-500 error handlers.
Driven with a fake db whose create_shift raises the relevant exception."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.master_data as md


class _ShiftDB:
    def __init__(self, exc=None):
        self._exc = exc

    def create_shift(self, data):
        if self._exc:
            raise self._exc
        return {"ID": 1, "NAME": data.get("NAME")}


def _admin_session():
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 920, "NAME": "md_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(md, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


class TestCreateShift:
    _URL = "/api/shifts"

    def _post(self, client, tok, name="Frühschicht"):
        return client.post(self._URL, json={"NAME": name}, headers={"X-Auth-Token": tok})

    def test_blank_name_returns_400(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _admin_session()
        try:
            # whitespace passes min_length=1 but fails the explicit strip() guard
            resp = self._post(_client(monkeypatch, _ShiftDB()), tok, name="   ")
            assert resp.status_code == 400
        finally:
            _sessions.pop(tok, None)

    def test_duplicate_shift_name_returns_409(self, monkeypatch):
        from sp5api.main import _sessions

        db = _ShiftDB(ValueError("DUPLICATE:SHIFTNAME:Frühschicht"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 409
            assert "existiert bereits" in resp.json()["detail"]
        finally:
            _sessions.pop(tok, None)

    def test_generic_value_error_is_sanitized_500(self, monkeypatch):
        from sp5api.main import _sessions

        db = _ShiftDB(ValueError("some other problem"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "some other problem" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_unexpected_error_is_sanitized_500(self, monkeypatch):
        from sp5api.main import _sessions

        db = _ShiftDB(RuntimeError("db boom"))
        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "db boom" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_success_returns_record(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _admin_session()
        try:
            resp = self._post(_client(monkeypatch, _ShiftDB()), tok)
            assert resp.status_code == 200
            assert resp.json()["record"]["NAME"] == "Frühschicht"
        finally:
            _sessions.pop(tok, None)


class _CaptureDB:
    """Fängt die an die lib weitergereichten Felder ab (Round-Trip-Beweis)."""

    def __init__(self):
        self.captured = None

    def create_shift(self, data):
        self.captured = data
        return {"ID": 1, "NAME": data.get("NAME")}

    def update_shift(self, shift_id, data):
        self.captured = data
        return {"ID": shift_id, "NAME": "X"}

    def create_leave_type(self, data):
        self.captured = data
        return {"ID": 1, "NAME": data.get("NAME")}

    def update_leave_type(self, lid, data):
        self.captured = data
        return {"ID": lid, "NAME": "X"}


class TestBoldForwarded:
    """P-VOLLERFASSUNG Lücke #10: das Fettschrift-Flag (5SHIFT.BOLD / 5LEAVT.BOLD)
    wird von der API an die lib weitergereicht."""

    def test_create_shift_forwards_bold(self, monkeypatch):
        from sp5api.main import _sessions

        db = _CaptureDB()
        tok = _admin_session()
        try:
            client = _client(monkeypatch, db)
            client.post(
                "/api/shifts",
                json={"NAME": "Fett", "BOLD": 1},
                headers={"X-Auth-Token": tok},
            )
            assert db.captured["BOLD"] == 1
        finally:
            _sessions.pop(tok, None)

    def test_update_shift_forwards_bold_zero(self, monkeypatch):
        from sp5api.main import _sessions

        db = _CaptureDB()
        tok = _admin_session()
        try:
            client = _client(monkeypatch, db)
            client.put(
                "/api/shifts/7",
                json={"BOLD": 0},
                headers={"X-Auth-Token": tok},
            )
            # 0 ist nicht None → muss durchgereicht werden (sonst nie abschaltbar)
            assert db.captured["BOLD"] == 0
        finally:
            _sessions.pop(tok, None)

    def test_create_leave_type_forwards_bold(self, monkeypatch):
        from sp5api.main import _sessions

        db = _CaptureDB()
        tok = _admin_session()
        try:
            client = _client(monkeypatch, db)
            client.post(
                "/api/leave-types",
                json={"NAME": "FettAbw", "BOLD": 1},
                headers={"X-Auth-Token": tok},
            )
            assert db.captured["BOLD"] == 1
        finally:
            _sessions.pop(tok, None)
