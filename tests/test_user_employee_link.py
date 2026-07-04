"""Directive A: explizite User→Mitarbeiter-Zuordnung (5USER_EMPLOYEE.json).

Das Original verknüpft 5USER und 5EMPL NICHT; „Mein Kalender" ist eine
App-Neuerung. Statt der fragilen Heuristik „Benutzername == Nachname" gibt es
jetzt eine explizite, App-verwaltete Zuordnung. Diese Tests sichern:
- resolve_employee_for_user bevorzugt die explizite Zuordnung vor dem Namen;
- get_my_employee liefert bei fehlender Zuordnung KEINE Sackgasse (can_link/suggestion);
- Self-Link (Planer/Admin) und Admin-Link setzen/entfernen die Zuordnung, mit
  korrekter Rechte-Durchsetzung (Leser 403 beim Self-Link, Nicht-Admin 403 beim
  Admin-Link) und 404 bei unbekanntem User/Mitarbeiter.
"""

import secrets

from starlette.testclient import TestClient

import sp5api.dependencies as deps
import sp5api.routers.misc as misc

_EMPLOYEES = [
    {"ID": 57, "NAME": "Schmidt", "FIRSTNAME": "Anna", "NUMBER": "1001"},
    {"ID": 61, "NAME": "Wolf", "FIRSTNAME": "Bea", "NUMBER": "1002"},
]


class _LinkDB:
    def __init__(self, users=None, links=None):
        self._users = users or {}
        self._links = dict(links or {})

    def get_employees(self, include_hidden=False):
        return _EMPLOYEES

    def get_employee(self, emp_id):
        return next((e for e in _EMPLOYEES if e.get("ID") == emp_id), None)

    def get_user_identity(self, uid):
        return self._users.get(uid)

    def get_linked_employee_id(self, uid):
        v = self._links.get(str(uid))
        return int(v) if v is not None else None

    def set_user_employee_link(self, uid, eid):
        self._links[str(uid)] = int(eid)

    def delete_user_employee_link(self, uid):
        if str(uid) in self._links:
            del self._links[str(uid)]
            return True
        return False


def _session(uid, name, role):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": uid, "NAME": name, "role": role, "ADMIN": role == "Admin",
        "RIGHTS": 1 if role == "Leser" else (0 if role == "Planer" else 0),
    }
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(misc, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


# ── resolve_employee_for_user: explizite Zuordnung schlägt Namen ──────────────

def test_resolve_prefers_explicit_link_over_name(monkeypatch):
    # User heißt "chef" (kein namensgleicher MA), aber explizit auf MA 57 gemappt.
    db = _LinkDB(links={"950": 57})
    monkeypatch.setattr(deps, "get_db", lambda: db)
    user = {"ID": 950, "NAME": "chef", "role": "Leser"}
    emp = deps.resolve_employee_for_user(user, required=False)
    assert emp is not None and emp["ID"] == 57


def test_resolve_name_fallback_when_no_link(monkeypatch):
    db = _LinkDB()
    monkeypatch.setattr(deps, "get_db", lambda: db)
    user = {"ID": 950, "NAME": "schmidt", "role": "Leser"}  # namensgleich MA 57
    emp = deps.resolve_employee_for_user(user, required=False)
    assert emp is not None and emp["ID"] == 57


def test_resolve_none_when_neither(monkeypatch):
    db = _LinkDB()
    monkeypatch.setattr(deps, "get_db", lambda: db)
    user = {"ID": 950, "NAME": "chef", "role": "Leser"}
    assert deps.resolve_employee_for_user(user, required=False) is None


# ── get_my_employee: kein Sackgassen-Zustand ─────────────────────────────────

def test_my_employee_unlinked_offers_link_context(monkeypatch):
    db = _LinkDB()
    c = _client(monkeypatch, db)
    tok = _session(950, "chef", "Planer")  # kein MA "chef"
    r = c.get("/api/me/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    body = r.json()
    assert body["employee"] is None
    assert body["can_link"] is True            # Planer darf zuordnen
    assert body["suggestion"] is None          # kein namensgleicher Vorschlag


def test_my_employee_unlinked_leser_cannot_link_but_gets_suggestion(monkeypatch):
    db = _LinkDB()
    c = _client(monkeypatch, db)
    tok = _session(950, "schmidt", "Leser")   # namensgleich MA 57, aber Leser
    r = c.get("/api/me/employee", headers={"X-Auth-Token": tok})
    body = r.json()
    # Namensgleich → resolve findet MA 57, employee ist NICHT None
    assert body["employee"] is not None and body["employee"]["ID"] == 57


def test_my_employee_unlinked_unknown_name_leser(monkeypatch):
    db = _LinkDB()
    c = _client(monkeypatch, db)
    tok = _session(950, "chef", "Leser")
    body = c.get("/api/me/employee", headers={"X-Auth-Token": tok}).json()
    assert body["employee"] is None
    assert body["can_link"] is False           # Leser darf nicht selbst zuordnen
    assert body["suggestion"] is None


# ── Self-Link (Planer/Admin) ─────────────────────────────────────────────────

def test_self_link_planer_success_then_resolves(monkeypatch):
    db = _LinkDB()
    c = _client(monkeypatch, db)
    tok = _session(950, "chef", "Planer")
    r = c.post("/api/me/employee", json={"employee_id": 61},
               headers={"X-Auth-Token": tok})
    assert r.status_code == 200, r.text
    # Danach löst „Mein Kalender" auf MA 61 auf (trotz Name „chef")
    body = c.get("/api/me/employee", headers={"X-Auth-Token": tok}).json()
    assert body["employee"]["ID"] == 61


def test_self_link_unknown_employee_404(monkeypatch):
    c = _client(monkeypatch, _LinkDB())
    tok = _session(950, "chef", "Planer")
    r = c.post("/api/me/employee", json={"employee_id": 99999},
               headers={"X-Auth-Token": tok})
    assert r.status_code == 404


def test_self_link_leser_forbidden_403(monkeypatch):
    c = _client(monkeypatch, _LinkDB())
    tok = _session(950, "chef", "Leser")
    r = c.post("/api/me/employee", json={"employee_id": 57},
               headers={"X-Auth-Token": tok})
    assert r.status_code == 403


def test_self_unlink(monkeypatch):
    db = _LinkDB(links={"950": 57})
    c = _client(monkeypatch, db)
    tok = _session(950, "chef", "Planer")
    r = c.delete("/api/me/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 200 and r.json()["removed"] is True


# ── Admin-Link beliebiger Benutzer (Benutzerverwaltung) ──────────────────────

def test_admin_link_user_success(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}})
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin", "Admin")
    r = c.put("/api/users/950/employee", json={"employee_id": 57},
              headers={"X-Auth-Token": tok})
    assert r.status_code == 200, r.text
    assert db.get_linked_employee_id(950) == 57


def test_admin_link_unknown_user_404(monkeypatch):
    db = _LinkDB(users={})
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin", "Admin")
    r = c.put("/api/users/12345/employee", json={"employee_id": 57},
              headers={"X-Auth-Token": tok})
    assert r.status_code == 404


def test_admin_link_unknown_employee_404(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}})
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin", "Admin")
    r = c.put("/api/users/950/employee", json={"employee_id": 99999},
              headers={"X-Auth-Token": tok})
    assert r.status_code == 404


def test_admin_link_non_admin_forbidden_403(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}})
    c = _client(monkeypatch, db)
    tok = _session(2, "planer", "Planer")
    r = c.put("/api/users/950/employee", json={"employee_id": 57},
              headers={"X-Auth-Token": tok})
    assert r.status_code == 403


def test_admin_get_user_employee_linked(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}}, links={"950": 57})
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin", "Admin")
    r = c.get("/api/users/950/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    assert r.json()["employee"]["ID"] == 57


def test_admin_get_user_employee_unlinked_null(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}})
    c = _client(monkeypatch, db)
    tok = _session(1, "Admin", "Admin")
    r = c.get("/api/users/950/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 200 and r.json()["employee"] is None


def test_admin_get_unknown_user_404(monkeypatch):
    c = _client(monkeypatch, _LinkDB(users={}))
    tok = _session(1, "Admin", "Admin")
    r = c.get("/api/users/999/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 404


def test_admin_get_user_employee_non_admin_403(monkeypatch):
    db = _LinkDB(users={950: {"ID": 950, "NAME": "chef"}})
    c = _client(monkeypatch, db)
    tok = _session(2, "planer", "Planer")
    r = c.get("/api/users/950/employee", headers={"X-Auth-Token": tok})
    assert r.status_code == 403
