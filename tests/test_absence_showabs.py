"""A2 / Item (36): `GET /api/absences` respektiert die SHOWABS-Sichtbarkeit
(Spec 9.5.2 Nr. 2.1) genauso wie das Dienstplan-Gitter (schedule.py) — die Liste
darf die Abwesenheitsart eines anonymisiert/ausgeblendet berechtigten Benutzers
nicht durchreichen.

  mode 0 (Admin/unbeschränkt) = echte Art;
  mode 1 = anonymisiert (Art unkenntlich, leave_type_id entfernt);
  mode 2 = ausgeblendet (leere Liste).
"""

import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.absences as absences_router
import sp5api.scopes as scopes

_ABSENCES = [
    {"id": 1, "employee_id": 65, "date": "2026-07-15",
     "leave_type_id": 3, "leave_type_name": "Krankheit", "leave_type_short": "Kr"},
]


class _AnonDB:
    """Double: get_absences_list liefert Rohdaten; anonymize_absence_rows spiegelt
    die lib-Semantik, damit der Test die Router-VERDRAHTUNG (abs_mode) prüft."""

    def get_absences_list(self, year=None, employee_id=None, leave_type_id=None):
        return [dict(a) for a in _ABSENCES]

    def get_user_visible_employee_ids(self, uid):
        return None  # unbeschränkter Scope → nur der Modus wirkt

    def anonymize_absence_rows(self, rows, mode):
        if mode == 0 or not rows:
            return rows
        if mode == 2:
            return []
        return [
            {**r, "leave_type_id": None,
             "leave_type_name": "Abwesend", "leave_type_short": "X"}
            for r in rows
        ]


def _session(uid, role, showabs_mode=0):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": uid, "NAME": "gl", "role": role,
        "ADMIN": role == "Admin", "RIGHTS": 1, "SHOWABS_MODE": showabs_mode,
    }
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(absences_router, "get_db", lambda: db)
    monkeypatch.setattr(scopes, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


def test_showabs1_anonymises_list(monkeypatch):
    c = _client(monkeypatch, _AnonDB())
    tok = _session(950, "Leser", showabs_mode=1)
    r = c.get("/api/absences", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    a = r.json()[0]
    assert a["leave_type_id"] is None
    assert a["leave_type_name"] == "Abwesend"
    assert a["leave_type_short"] == "X"


def test_showabs2_hides_list(monkeypatch):
    c = _client(monkeypatch, _AnonDB())
    tok = _session(950, "Leser", showabs_mode=2)
    r = c.get("/api/absences", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    assert r.json() == []


def test_admin_sees_real_type(monkeypatch):
    c = _client(monkeypatch, _AnonDB())
    tok = _session(1, "Admin", showabs_mode=0)
    r = c.get("/api/absences", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    a = r.json()[0]
    assert a["leave_type_name"] == "Krankheit" and a["leave_type_id"] == 3
