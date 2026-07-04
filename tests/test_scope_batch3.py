"""Item (29) Batch 3 — Scope-Konsistenz bei Qualifikationsmatrix, wiederkehrenden
Diensten und Arbeitszeitregel-Prüfung (5GRACC/5EMACC, Spec 9.5.3).
Eingeschränkte Benutzer sehen/prüfen nur ihre sichtbaren Mitarbeiter.
"""

import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.qualification_matrix as qm
import sp5api.routers.recurring_shifts as rs
import sp5api.routers.work_time_rules as wtr
import sp5api.scopes as scopes

_EMPLOYEES = [
    {"ID": 65, "NAME": "In", "FIRSTNAME": "A", "GROUPID": 51, "NOTE1": "Erste Hilfe,Stapler"},
    {"ID": 99, "NAME": "Out", "FIRSTNAME": "B", "GROUPID": 51, "NOTE1": "Stapler"},
]


class _ScopeDB:
    def __init__(self, visible):
        self._visible = visible

    def get_user_visible_employee_ids(self, uid):
        return set(self._visible) if self._visible is not None else None

    def get_employees(self, include_hidden=False):
        return [dict(e) for e in _EMPLOYEES]

    def get_employee(self, emp_id):
        return next((dict(e) for e in _EMPLOYEES if e["ID"] == emp_id), None)

    def get_all_group_members(self):
        return {51: [65, 99]}

    def get_groups(self, include_hidden=True):
        return [{"ID": 51, "NAME": "G"}]

    def get_group_members(self, gid):
        return [65, 99]


def _session(uid, role):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": uid, "NAME": "gl", "role": role, "ADMIN": role == "Admin", "RIGHTS": 0}
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    for mod in (qm, rs, wtr, deps, scopes):
        monkeypatch.setattr(mod, "get_db", lambda: db, raising=False)
    return TestClient(app, raise_server_exceptions=False)


def _H(tok):
    return {"X-Auth-Token": tok}


# ── qualification_matrix ─────────────────────────────────────────────────────

def test_qualification_matrix_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/employees/qualification-matrix", headers=_H(tok))
    assert r.status_code == 200
    assert {row["id"] for row in r.json()["employees"]} == {65}


def test_qualification_stats_scoped(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/qualifications/stats", headers=_H(tok))
    assert r.status_code == 200
    # „Erste Hilfe" hat nur MA 65 → taucht nur auf, wenn 65 sichtbar ist
    ids = {e["id"] for q in r.json()["qualifications"] for e in q["employees"]}
    assert ids == {65}


# ── recurring_shifts ─────────────────────────────────────────────────────────

def test_recurring_list_scoped(monkeypatch):
    monkeypatch.setattr(rs, "_read_all", lambda: [
        {"id": 1, "employee_id": 65}, {"id": 2, "employee_id": 99},
    ])
    monkeypatch.setattr(rs, "_enrich", lambda db, p: p)
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.get("/api/shifts/recurring", headers=_H(tok))
    assert r.status_code == 200
    assert {p["employee_id"] for p in r.json()} == {65}


# ── work_time_rules ──────────────────────────────────────────────────────────

def test_wtr_check_employee_out_of_scope_404(monkeypatch):
    monkeypatch.setattr(wtr, "_load_rules", lambda: {})
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.post("/api/work-time-rules/check?employee_id=99&from=2026-07-01&to=2026-07-31", headers=_H(tok))
    assert r.status_code == 404


def test_wtr_check_all_scoped(monkeypatch):
    called = []
    monkeypatch.setattr(wtr, "_load_rules", lambda: {})
    monkeypatch.setattr(wtr, "_check_employee", lambda db, eid, f, t, rules: called.append(eid) or [])
    c = _client(monkeypatch, _ScopeDB(visible={65}))
    tok = _session(950, "Planer")
    r = c.post("/api/work-time-rules/check-all?from=2026-07-01&to=2026-07-31", headers=_H(tok))
    assert r.status_code == 200
    assert called == [65]  # nur der sichtbare MA wurde geprüft


def test_admin_unrestricted_matrix_all(monkeypatch):
    c = _client(monkeypatch, _ScopeDB(visible=None))
    tok = _session(1, "Admin")
    r = c.get("/api/employees/qualification-matrix", headers=_H(tok))
    assert {row["id"] for row in r.json()["employees"]} == {65, 99}
