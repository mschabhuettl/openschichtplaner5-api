"""Item (29) Scope-Konsistenz: `GET /api/absences` respektiert die
differenzierte Sichtbarkeit (5GRACC/5EMACC, Spec 9.5.3) wie das Dienstplan-
Gitter. Ein eingeschränkter Benutzer sieht nur Abwesenheiten seiner sichtbaren
Mitarbeiter — auch nicht via `?employee_id=` außerhalb seines Scopes.
"""

import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.absences as absences_router
import sp5api.scopes as scopes

_ABSENCES = [
    {"id": 1, "employee_id": 65, "date": "2026-07-10", "leave_type_id": 1},
    {"id": 2, "employee_id": 99, "date": "2026-07-11", "leave_type_id": 1},  # out of scope
]


class _ScopeDB:
    def __init__(self, visible):
        self._visible = visible  # set | None

    def get_absences_list(self, year=None, employee_id=None, leave_type_id=None):
        rows = _ABSENCES
        if employee_id is not None:
            rows = [a for a in rows if a["employee_id"] == employee_id]
        return rows

    def get_user_visible_employee_ids(self, uid):
        return set(self._visible) if self._visible is not None else None

    def anonymize_absence_rows(self, rows, mode):
        # list_absences ruft dies nach dem Scope-Filter (SHOWABS-Sichtbarkeit);
        # diese Tests laufen mit Modus 0 → Durchreichen. Vertrag gespiegelt.
        if mode == 0 or not rows:
            return rows
        if mode == 2:
            return []
        return [{**r, "leave_type_id": None} for r in rows]


def _session(uid, role):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": uid, "NAME": "gl", "role": role,
        "ADMIN": role == "Admin", "RIGHTS": 1,
    }
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(absences_router, "get_db", lambda: db)
    monkeypatch.setattr(scopes, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


def test_restricted_user_sees_only_in_scope_absences(monkeypatch):
    db = _ScopeDB(visible={65})  # nur MA 65 sichtbar
    c = _client(monkeypatch, db)
    tok = _session(950, "Planer")
    r = c.get("/api/absences", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    ids = {a["employee_id"] for a in r.json()}
    assert ids == {65}


def test_restricted_user_cannot_read_out_of_scope_via_employee_id(monkeypatch):
    db = _ScopeDB(visible={65})
    c = _client(monkeypatch, db)
    tok = _session(950, "Planer")
    r = c.get("/api/absences?employee_id=99", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    assert r.json() == []  # MA 99 außerhalb des Scopes → nichts


def test_admin_unrestricted_sees_all(monkeypatch):
    db = _ScopeDB(visible=None)
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin")
    r = c.get("/api/absences", headers={"X-Auth-Token": tok})
    ids = {a["employee_id"] for a in r.json()}
    assert ids == {65, 99}
