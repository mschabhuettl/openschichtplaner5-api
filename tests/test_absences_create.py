"""Tests for create_absence (POST /api/absences) — the conflict/holiday
warning logic, the pending-status + planner-notification side effects, and the
overlap-conflict 409. Driven with a fake db; the status-file and notification
side effects are patched to no-ops."""

import secrets

import api.routers.absences as absences
from starlette.testclient import TestClient


class _AbsDB:
    def __init__(self, *, schedule_day=None, holidays=None, add_exc=None):
        self._schedule_day = schedule_day or []
        self._holidays = holidays or []
        self._add_exc = add_exc

    def get_employee(self, eid):
        return {"ID": eid, "NAME": "Müller", "FIRSTNAME": "Anna"}

    def get_leave_type(self, ltid):
        return {"ID": ltid, "NAME": "Urlaub"}

    def get_schedule_day(self, date):
        return self._schedule_day

    def get_holiday_dates(self, year):
        return self._holidays

    def add_absence(self, eid, date, ltid):
        if self._add_exc:
            raise self._add_exc
        return {"ID": 1}

    def log_action(self, **kwargs):
        pass


def _planer_session():
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 910,
        "NAME": "abs_planer",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    return tok


def _client(monkeypatch, db):
    from api.main import app

    monkeypatch.setattr(absences, "get_db", lambda: db)
    monkeypatch.setattr(absences, "create_notification", lambda **kwargs: None)
    monkeypatch.setattr(absences, "_load_absence_status", lambda: {})
    monkeypatch.setattr(absences, "_save_absence_status", lambda data: None)
    return TestClient(app, raise_server_exceptions=False)


class TestCreateAbsence:
    def _post(self, client, tok, eid=5, date="2026-07-15", ltid=1):
        return client.post(
            "/api/absences",
            json={"employee_id": eid, "date": date, "leave_type_id": ltid},
            headers={"X-Auth-Token": tok},
        )

    def test_warns_about_existing_shift_and_holiday(self, monkeypatch):
        from api.main import _sessions

        db = _AbsDB(
            schedule_day=[{"employee_id": 5, "kind": "shift", "shift_name": "Frühdienst"}],
            holidays=["2026-07-15"],
        )
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 200
            warnings = resp.json()["warnings"]
            assert any("Schicht" in w for w in warnings)
            assert any("Feiertag" in w for w in warnings)
        finally:
            _sessions.pop(tok, None)

    def test_overlap_conflict_returns_409(self, monkeypatch):
        from api.main import _sessions

        db = _AbsDB(add_exc=ValueError("overlap"))
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 409
        finally:
            _sessions.pop(tok, None)

    def test_side_effect_failures_never_block_creation(self, monkeypatch):
        # Warning lookup, status-file write and notification all blow up, yet the
        # absence is still created (each is wrapped in a swallow-and-continue guard).
        from api.main import _sessions, app

        class _BoomWarn(_AbsDB):
            def get_schedule_day(self, date):
                raise RuntimeError("warning lookup failed")

        monkeypatch.setattr(absences, "get_db", lambda: _BoomWarn())
        monkeypatch.setattr(absences, "_load_absence_status", lambda: {})
        monkeypatch.setattr(
            absences,
            "_save_absence_status",
            lambda data: (_ for _ in ()).throw(RuntimeError("status write failed")),
        )
        monkeypatch.setattr(
            absences,
            "create_notification",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("notify failed")),
        )
        tok = _planer_session()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            client.headers["X-Auth-Token"] = tok
            resp = client.post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1},
            )
            assert resp.status_code == 200
        finally:
            _sessions.pop(tok, None)

    def test_unexpected_db_error_returns_sanitized_500(self, monkeypatch):
        from api.main import _sessions

        db = _AbsDB(add_exc=RuntimeError("db boom"))
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "db boom" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_employee_not_found_404(self, monkeypatch):
        from api.main import _sessions

        class _NoEmp(_AbsDB):
            def get_employee(self, eid):
                return None

        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, _NoEmp()), tok)
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_leave_type_not_found_404(self, monkeypatch):
        from api.main import _sessions

        class _NoLt(_AbsDB):
            def get_leave_type(self, ltid):
                return None

        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, _NoLt()), tok)
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)
