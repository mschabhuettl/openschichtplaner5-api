"""SP5_READONLY: rein lesende Instanz — serverseitig erzwungen.

Bei aktivem Flag lehnt die Middleware JEDE Schreibmethode auf /api/* mit 403
ab (auch für Admin), noch vor dem Routing. Session-Steuerung (Login/Logout)
bleibt nutzbar. Default (Flag aus) verhält sich unverändert.
"""

import pytest
from starlette.testclient import TestClient

import sp5api.main as main_module


@pytest.fixture
def readonly_on(monkeypatch):
    monkeypatch.setattr(main_module, "_READONLY", True)


def _write_routes():
    """Alle Schreib-Routen der App aus dem Router-Bestand (Beweis-Basis)."""
    routes = []
    for route in main_module.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        for m in methods & {"POST", "PUT", "PATCH", "DELETE"}:
            routes.append((m, path))
    return routes


class TestReadonlyEnforced:
    def test_every_write_route_rejected(self, admin_client: TestClient, readonly_on):
        routes = _write_routes()
        assert len(routes) > 100  # Vollständigkeits-Plausibilität
        exempt = ("/auth/logout", "/auth/impersonate", "/auth/login", "/csp-report")
        failures = []
        for method, path in routes:
            if any(e in path for e in exempt):
                continue
            url = path
            for name in ("employee_id", "user_id", "emp_id", "shift_id", "group_id",
                         "cycle_id", "note_id", "wish_id", "request_id", "template_id",
                         "report_id", "company_id", "webhook_id", "ban_id", "item_id",
                         "workplace_id", "leave_type_id", "extracharge_id", "period_id",
                         "restriction_id", "absence_id", "entry_id", "backup_name",
                         "token", "date", "employee_date", "filename", "table_name", "id"):
                url = url.replace("{" + name + "}", "1")
            if "{" in url:
                import re
                url = re.sub(r"\{[^}]+\}", "1", url)
            res = admin_client.request(method, url)
            if res.status_code != 403 or "schreibgeschützt" not in res.text:
                failures.append((method, url, res.status_code))
        assert failures == []

    def test_admin_cannot_write(self, admin_client: TestClient, readonly_on):
        res = admin_client.post("/api/employees", json={"NAME": "X", "FIRSTNAME": "Y"})
        assert res.status_code == 403
        assert "schreibgeschützt" in res.json()["detail"]

    def test_login_and_logout_still_work(self, admin_client: TestClient, readonly_on, sync_client: TestClient):
        # Login (Public-Path) bleibt möglich
        res = sync_client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert res.status_code in (200, 401)  # 401 nur bei anderem Testpasswort — nicht 403
        assert res.status_code != 403
        # Logout (Session-Steuerung) bleibt möglich
        res2 = admin_client.post("/api/auth/logout")
        assert res2.status_code != 403

    def test_reads_still_work(self, admin_client: TestClient, readonly_on):
        res = admin_client.get("/api/employees")
        assert res.status_code == 200

    def test_health_reports_flag(self, sync_client: TestClient, readonly_on):
        res = sync_client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["readonly"] is True


class TestReadonlyDefaultOff:
    def test_default_allows_writes(self, admin_client: TestClient):
        assert main_module._READONLY is False
        res = admin_client.post("/api/employees", json={"NAME": "Norm", "FIRSTNAME": "Betrieb"})
        assert res.status_code == 200

    def test_health_reports_flag_off(self, sync_client: TestClient):
        res = sync_client.get("/api/health")
        assert res.json()["readonly"] is False
