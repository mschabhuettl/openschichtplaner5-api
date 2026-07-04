"""Tests for the self-service endpoints in misc.py — a logged-in user reads
and submits their own schedule / wishes / absences. Each resolves the employee
by matching the session NAME, then scopes data to that employee. Driven with a
session + fake db."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.misc as misc


class _SelfDB:
    def __init__(
        self, *, employees=None, schedule=None, wishes=None, absences=None,
        add_wish_exc=None, leave_types=None,
    ):
        self._employees = employees if employees is not None else [{"ID": 7, "NAME": "selfuser"}]
        self._schedule = schedule or []
        self._wishes = wishes or []
        self._absences = absences or []
        self._add_wish_exc = add_wish_exc
        # Gültige Abwesenheitstypen; Default {1}, damit die Erfolgs-Tests laufen.
        self._leave_types = set(leave_types) if leave_types is not None else {1}

    def get_leave_type(self, lt_id):
        return {"ID": lt_id, "NAME": "Urlaub"} if lt_id in self._leave_types else None

    def get_employees(self, include_hidden=False):
        return self._employees

    def get_schedule(self, year, month):
        return self._schedule

    def get_wishes(self, **kwargs):
        return self._wishes

    def add_wish(self, **kwargs):
        if self._add_wish_exc:
            raise self._add_wish_exc
        return {"ok": True, **kwargs}

    def get_absences_list(self, employee_id):
        return self._absences

    def add_absence(self, eid, date, ltid):
        return {"ID": 1, "date": date}


def _session(name="selfuser"):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 950, "NAME": name, "role": "Leser", "ADMIN": False, "RIGHTS": 1}
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(misc, "get_db", lambda: db)
    c = TestClient(app, raise_server_exceptions=False)
    return c


class TestSelfSchedule:
    def test_invalid_month_400(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.get("/api/self/schedule?year=2026&month=13", headers={"X-Auth-Token": tok})
            assert resp.status_code == 400
        finally:
            _sessions.pop(tok, None)

    def test_returns_only_own_entries(self, monkeypatch):
        from sp5api.main import _sessions

        db = _SelfDB(
            schedule=[
                {"employee_id": 7, "date": "2026-07-01"},
                {"employee_id": 8, "date": "2026-07-02"},  # other employee
            ]
        )
        tok = _session()
        try:
            c = _client(monkeypatch, db)
            resp = c.get("/api/self/schedule?year=2026&month=7", headers={"X-Auth-Token": tok})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1 and data[0]["employee_id"] == 7
        finally:
            _sessions.pop(tok, None)

    def test_no_employee_record_404(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session(name="nobody")  # no matching employee
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.get("/api/self/schedule?year=2026&month=7", headers={"X-Auth-Token": tok})
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)


class TestSelfWishes:
    def test_get_own_wishes(self, monkeypatch):
        from sp5api.main import _sessions

        db = _SelfDB(wishes=[{"id": 1, "wish_type": "WUNSCH"}])
        tok = _session()
        try:
            c = _client(monkeypatch, db)
            resp = c.get("/api/self/wishes?year=2026&month=7", headers={"X-Auth-Token": tok})
            assert resp.status_code == 200
            assert resp.json()[0]["wish_type"] == "WUNSCH"
        finally:
            _sessions.pop(tok, None)

    def test_create_wish_success(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/wishes",
                json={"date": "2026-07-15", "wish_type": "wunsch", "shift_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 200
        finally:
            _sessions.pop(tok, None)

    def test_create_wish_invalid_type(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/wishes",
                json={"date": "2026-07-15", "wish_type": "BOGUS", "shift_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code in (400, 422)
        finally:
            _sessions.pop(tok, None)

    def test_create_wish_conflict_409(self, monkeypatch):
        from sp5api.main import _sessions

        db = _SelfDB(add_wish_exc=ValueError("already exists"))
        tok = _session()
        try:
            c = _client(monkeypatch, db)
            resp = c.post(
                "/api/self/wishes",
                json={"date": "2026-07-15", "wish_type": "WUNSCH", "shift_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 409
        finally:
            _sessions.pop(tok, None)

    def test_create_wish_no_employee_404(self, monkeypatch):
        """Konto ohne namensgleichen MA → 404 statt 500 (iCal-Bug-Klasse)."""
        from sp5api.main import _sessions

        tok = _session(name="nobody")
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/wishes",
                json={"date": "2026-07-15", "wish_type": "WUNSCH", "shift_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_get_wishes_no_employee_404(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session(name="nobody")
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.get("/api/self/wishes", headers={"X-Auth-Token": tok})
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_delete_wish_no_employee_404(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session(name="nobody")
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.delete("/api/self/wishes/1", headers={"X-Auth-Token": tok})
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_create_wish_invalid_calendar_date_422(self, monkeypatch):
        """Formal gültiges, kalendarisch ungültiges Datum (2026-04-31) darf NICHT
        in die DBF geschrieben werden — 422 statt 200 (Datenintegrität)."""
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/wishes",
                json={"date": "2026-04-31", "wish_type": "WUNSCH", "shift_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 422
        finally:
            _sessions.pop(tok, None)


class TestSelfAbsence:
    def test_create_absence_success(self, monkeypatch):
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/absences",
                json={"date": "2026-08-01", "leave_type_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 200
        finally:
            _sessions.pop(tok, None)

    def test_create_absence_duplicate_409(self, monkeypatch):
        from sp5api.main import _sessions

        db = _SelfDB(absences=[{"date": "2026-08-01"}])
        tok = _session()
        try:
            c = _client(monkeypatch, db)
            resp = c.post(
                "/api/self/absences",
                json={"date": "2026-08-01", "leave_type_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 409
        finally:
            _sessions.pop(tok, None)

    def test_create_absence_no_employee_404(self, monkeypatch):
        """Konto ohne namensgleichen MA → 404 statt 500 (iCal-Bug-Klasse)."""
        from sp5api.main import _sessions

        tok = _session(name="nobody")
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/absences",
                json={"date": "2026-08-01", "leave_type_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_create_absence_invalid_calendar_date_422(self, monkeypatch):
        """Kalendarisch ungültiges Datum (2026-04-31) → 422, nicht gespeichert."""
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())
            resp = c.post(
                "/api/self/absences",
                json={"date": "2026-04-31", "leave_type_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 422
        finally:
            _sessions.pop(tok, None)

    def test_create_absence_unknown_leave_type_404(self, monkeypatch):
        """Nicht existierender Abwesenheitstyp → 404 statt einer toten
        LEAVETYPID-Referenz in der DBF (Konsistenz mit POST /api/absences)."""
        from sp5api.main import _sessions

        tok = _session()
        try:
            c = _client(monkeypatch, _SelfDB())  # gültige Typen = {1}
            resp = c.post(
                "/api/self/absences",
                json={"date": "2026-09-11", "leave_type_id": 99999},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)


def test_wishcreate_model_rejects_invalid_calendar_date():
    """Planer-Modell WishCreate lehnt kalendarisch ungültige Daten ab (wie die
    Self-Service-Modelle) — sonst schreibt POST /api/wishes 2026-02-30 in die DBF."""
    import pytest
    from pydantic import ValidationError

    from sp5api.routers.misc import WishCreate

    assert WishCreate(employee_id=1, date="2026-06-30", wish_type="WUNSCH").date == "2026-06-30"
    with pytest.raises(ValidationError):
        WishCreate(employee_id=1, date="2026-02-30", wish_type="WUNSCH")
