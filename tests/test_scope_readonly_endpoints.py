"""Item (29) Scope-Konsistenz: Über-/Minusstunden und Verfügbarkeit
respektieren die differenzierte Sichtbarkeit (5GRACC/5EMACC, Spec 9.5.3).
Eingeschränkte Benutzer sehen nur ihre sichtbaren Mitarbeiter; verborgene
Mitarbeiter liefern 404 (wie GET /api/employees/{id}).
"""

import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.availability as availability_router
import sp5api.routers.overtime as overtime_router
import sp5api.scopes as scopes


class _ScopeDB:
    def __init__(self, visible):
        self._visible = visible  # set | None

    def get_user_visible_employee_ids(self, uid):
        return set(self._visible) if self._visible is not None else None

    def get_employee(self, emp_id):
        return {"ID": emp_id, "NAME": "X", "FIRSTNAME": "Y", "SHORTNAME": "XY", "HRSWEEK": 38.5}

    def get_employee_stats_month(self, emp_id, year, month):
        return {"target_hours": 154.0, "actual_hours": 160.0, "difference": 6.0, "shifts_count": 20}

    def get_employees(self, include_hidden=False):
        return [{"ID": 65, "HRSWEEK": 38.5}, {"ID": 99, "HRSWEEK": 38.5}]

    def get_statistics(self, year, month, group_id=None):
        return [
            {"employee_id": 65, "employee_name": "In, Scope", "employee_short": "IS",
             "target_hours": 154.0, "actual_hours": 160.0, "overtime_hours": 6.0, "shifts_count": 20},
            {"employee_id": 99, "employee_name": "Out, Scope", "employee_short": "OS",
             "target_hours": 154.0, "actual_hours": 150.0, "overtime_hours": -4.0, "shifts_count": 18},
        ]


def _session(uid, role):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": uid, "NAME": "gl", "role": role, "ADMIN": role == "Admin", "RIGHTS": 0}
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(overtime_router, "get_db", lambda: db)
    monkeypatch.setattr(availability_router, "get_db", lambda: db)
    monkeypatch.setattr(scopes, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


def _H(tok):
    return {"X-Auth-Token": tok}


def test_overtime_single_out_of_scope_404(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/employees/99/overtime?year=2026&month=7", headers=_H(tok))
    assert r.status_code == 404


def test_overtime_single_in_scope_200(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/employees/65/overtime?year=2026&month=7", headers=_H(tok))
    assert r.status_code == 200 and r.json()["employee_id"] == 65


def test_overtime_summary_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/overtime/summary?year=2026&month=7", headers=_H(tok))
    assert r.status_code == 200
    ids = {row["employee_id"] for row in r.json()["employees"]}
    assert ids == {65}


def test_availability_out_of_scope_404(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/employees/99/availability", headers=_H(tok))
    assert r.status_code == 404


def test_availability_in_scope_200(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/employees/65/availability", headers=_H(tok))
    assert r.status_code == 200 and r.json()["employee_id"] == 65


def test_admin_unrestricted_overtime_summary_all(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible=None))
    tok = _session(1, "Admin")
    r = c.get("/api/overtime/summary?year=2026&month=7", headers=_H(tok))
    ids = {row["employee_id"] for row in r.json()["employees"]}
    assert ids == {65, 99}
