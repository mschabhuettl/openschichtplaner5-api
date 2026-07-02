"""Berechnungsbasis Gesamtstunden (CALCBASE=3): nur mit geschlossenem
Beschäftigungszeitraum (EMPSTART+EMPEND) und HRSTOTAL > 0 zulässig —
sonst Erfassungsfehler wie im Original."""

from starlette.testclient import TestClient


class TestCalcbaseTotal:
    def test_create_rejects_total_without_period(self, admin_client: TestClient):
        res = admin_client.post("/api/employees", json={
            "NAME": "Gesamt", "FIRSTNAME": "Gerd", "CALCBASE": 3, "HRSTOTAL": 500,
        })
        assert res.status_code == 422
        assert "Gesamtstunden" in res.text

    def test_create_rejects_total_without_hours(self, admin_client: TestClient):
        res = admin_client.post("/api/employees", json={
            "NAME": "Gesamt", "FIRSTNAME": "Gerd", "CALCBASE": 3,
            "EMPSTART": "2026-01-01", "EMPEND": "2026-06-30", "HRSTOTAL": 0,
        })
        assert res.status_code == 422

    def test_create_accepts_valid_total(self, admin_client: TestClient):
        res = admin_client.post("/api/employees", json={
            "NAME": "Gesamt", "FIRSTNAME": "Gerd", "CALCBASE": 3,
            "EMPSTART": "2026-01-01", "EMPEND": "2026-06-30", "HRSTOTAL": 500,
        })
        assert res.status_code == 200, res.text
        rec = res.json().get("record") or {}
        assert rec.get("ID")
        assert rec.get("CALCBASE") == 3

    def test_update_rejects_switch_to_total_without_period(self, admin_client: TestClient):
        emps = admin_client.get("/api/employees").json()
        # MA ohne Austrittsdatum suchen
        emp = next(e for e in emps if not e.get("EMPEND"))
        res = admin_client.put(f"/api/employees/{emp['ID']}", json={"CALCBASE": 3})
        assert res.status_code == 400
        assert "Gesamtstunden" in res.json()["detail"]
