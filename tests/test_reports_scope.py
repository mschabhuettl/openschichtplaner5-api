"""Item (29) Batch 2 — Scope-Konsistenz bei Berichten/Statistik.

Handbuch/Spec 9.5.3: „Ergebnisse statistischer Berechnungen (in der
Personaltabelle und in Berichten)" unterliegen der differenzierten Sicht.
`/api/statistics`, `/api/personnel-table` und `/api/statistics/year-summary`
liefern eingeschränkten Benutzern nur ihre sichtbaren Mitarbeiter.
"""

import copy
import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.reports as reports_router
import sp5api.scopes as scopes

_STATS = [
    {"employee_id": 65, "employee_name": "In Scope", "actual_hours": 160.0,
     "target_hours": 154.0, "shifts_count": 20},
    {"employee_id": 99, "employee_name": "Out Scope", "actual_hours": 150.0,
     "target_hours": 154.0, "shifts_count": 18},
]
_TABLE = {
    "date_from": "2026-01-01", "date_to": "2026-12-31", "one_year": True,
    "columns": {"shifts": [], "leave_types": []},
    "rows": [{"employee_id": 65, "name": "In"}, {"employee_id": 99, "name": "Out"}],
}


class _ScopeDB:
    def __init__(self, visible):
        self._visible = visible

    def get_user_visible_employee_ids(self, uid):
        return set(self._visible) if self._visible is not None else None

    def get_statistics(self, year=None, month=None, group_id=None, date_from=None, date_to=None):
        return [dict(r) for r in _STATS]

    def get_personnel_table(self, date_from, date_to, group_id=None):
        return copy.deepcopy(_TABLE)


def _session(uid, role):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": uid, "NAME": "gl", "role": role, "ADMIN": role == "Admin", "RIGHTS": 1}
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(reports_router, "get_db", lambda: db)
    monkeypatch.setattr(scopes, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


def _H(tok):
    return {"X-Auth-Token": tok}


def test_statistics_month_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/statistics?year=2026&month=7", headers=_H(tok))
    assert r.status_code == 200
    assert {row["employee_id"] for row in r.json()} == {65}


def test_statistics_free_period_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/statistics?from=2026-01-01&to=2026-12-31", headers=_H(tok))
    assert r.status_code == 200
    assert {row["employee_id"] for row in r.json()} == {65}


def test_personnel_table_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/personnel-table?from=2026-01-01&to=2026-12-31", headers=_H(tok))
    assert r.status_code == 200
    assert {row["employee_id"] for row in r.json()["rows"]} == {65}


def test_year_summary_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/statistics/year-summary?year=2026", headers=_H(tok))
    assert r.status_code == 200
    body = r.json()
    assert {e["employee_id"] for e in body["employees"]} == {65}
    assert all(m["employee_count"] == 1 for m in body["monthly"])


def test_admin_unrestricted_sees_all(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible=None))
    tok = _session(1, "Admin")
    r = c.get("/api/statistics?year=2026&month=7", headers=_H(tok))
    assert {row["employee_id"] for row in r.json()} == {65, 99}
