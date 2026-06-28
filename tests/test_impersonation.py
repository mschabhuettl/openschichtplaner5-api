"""P-B Admin-Impersonation („Als Benutzer ansehen"): server-erzwungen, ohne
Rechte-Eskalation, read-only, nicht verschachtelbar, beide Richtungen.

Leitprinzip: Impersonation ist KEIN neuer Login/Token — der Admin behält seine
Session; nur der Autorisierungs-Principal (get_current_user) wird auf die Ziel-
Identität abgebildet. Revert-rot:
- ohne den get_current_user-Map-Punkt → /me bleibt Admin (Test 1/4 rot)
- ohne den Read-only-Block in auth_middleware → Writes nicht 403 (Test 2 rot)
- ohne den Non-Nest-Guard → zweiter Start erlaubt (Test 3 rot)
"""

import secrets

import pytest
from starlette.testclient import TestClient


def _inject(role="Admin", name=None, **flags):
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 970 if role == "Admin" else 971,
        "NAME": name or f"imp_{role.lower()}",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else 1,
        **flags,
    }
    return tok


@pytest.fixture
def client(write_db_path, app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _h(tok):
    return {"X-Auth-Token": tok}


def _make_user(client, admin_tok, name, role):
    r = client.post(
        "/api/users",
        json={"NAME": name, "PASSWORD": "Geheim123", "role": role},
        headers=_h(admin_tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["record"]["ID"]


class TestImpersonation:
    def test_start_maps_to_target_and_stop_restores(self, client):
        from sp5api.main import _sessions

        admin = _inject(role="Admin", name="imp_admin")
        try:
            lid = _make_user(client, admin, "imp_target_leser", "Leser")
            # Start
            r = client.post(f"/api/auth/impersonate/{lid}", headers=_h(admin))
            assert r.status_code == 200, r.text
            assert r.json()["impersonating"] == "imp_target_leser"
            # /me liefert jetzt die Ziel-Identität (Map-Punkt)
            me = client.get("/api/auth/me", headers=_h(admin)).json()
            assert me["role"] == "Leser"
            assert me["ID"] == lid
            assert me["_impersonation_active"] is True
            assert me["_impersonated_by"]["NAME"] == "imp_admin"
            # Stop → zurück zur Admin-Identität
            assert client.post("/api/auth/impersonate/stop", headers=_h(admin)).status_code == 200
            me = client.get("/api/auth/me", headers=_h(admin)).json()
            assert me["role"] == "Admin"
            assert me.get("_impersonation_active") is None
        finally:
            _sessions.pop(admin, None)

    def test_writes_blocked_even_for_writer_target(self, client):
        """Read-only-Block greift selbst wenn das ZIEL Schreibrechte hätte —
        unterscheidbar vom Rechte-Gate-403 über die read-only-Meldung."""
        from sp5api.main import _sessions

        admin = _inject(role="Admin", name="imp_admin_w")
        try:
            pid = _make_user(client, admin, "imp_target_planer", "Planer")
            client.post(f"/api/auth/impersonate/{pid}", headers=_h(admin))
            r = client.post(
                "/api/absences",
                json={"employee_id": 40, "date": "2099-06-01", "leave_type_id": 1},
                headers=_h(admin),
            )
            assert r.status_code == 403, r.text
            assert "read-only" in r.text.lower()
            # Stop ist als POST ausdrücklich erlaubt
            assert client.post("/api/auth/impersonate/stop", headers=_h(admin)).status_code == 200
            # Nach Stop schreibt der Admin wieder (kein read-only-Block mehr)
            r2 = client.post(
                "/api/absences",
                json={"employee_id": 40, "date": "2099-06-02", "leave_type_id": 1},
                headers=_h(admin),
            )
            assert r2.status_code != 403, r2.text
        finally:
            _sessions.pop(admin, None)

    def test_not_nestable(self, client):
        from sp5api.main import _sessions

        admin = _inject(role="Admin", name="imp_admin_nest")
        try:
            # Ziel Leser: nach Start ist der Principal Leser → zweiter Start
            # scheitert schon an require_admin (403)
            lid = _make_user(client, admin, "imp_nest_leser", "Leser")
            client.post(f"/api/auth/impersonate/{lid}", headers=_h(admin))
            r = client.post(f"/api/auth/impersonate/{lid}", headers=_h(admin))
            assert r.status_code == 403, r.text
            client.post("/api/auth/impersonate/stop", headers=_h(admin))
            # Ziel Admin: Principal bleibt Admin → expliziter Roh-Session-Guard 409
            aid = _make_user(client, admin, "imp_nest_admin", "Admin")
            client.post(f"/api/auth/impersonate/{aid}", headers=_h(admin))
            r = client.post(f"/api/auth/impersonate/{aid}", headers=_h(admin))
            assert r.status_code == 409, r.text
            client.post("/api/auth/impersonate/stop", headers=_h(admin))
        finally:
            _sessions.pop(admin, None)

    def test_non_admin_cannot_start(self, client):
        from sp5api.main import _sessions

        admin = _inject(role="Admin", name="imp_admin_seed")
        leser = _inject(role="Leser", name="imp_plain_leser")
        try:
            tid = _make_user(client, admin, "imp_seed_target", "Leser")
            r = client.post(f"/api/auth/impersonate/{tid}", headers=_h(leser))
            assert r.status_code == 403, r.text
        finally:
            _sessions.pop(admin, None)
            _sessions.pop(leser, None)

    def test_unknown_target_404(self, client):
        from sp5api.main import _sessions

        admin = _inject(role="Admin", name="imp_admin_404")
        try:
            r = client.post("/api/auth/impersonate/999999", headers=_h(admin))
            assert r.status_code == 404, r.text
        finally:
            _sessions.pop(admin, None)
