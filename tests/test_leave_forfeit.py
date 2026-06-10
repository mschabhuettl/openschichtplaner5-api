"""Tests für POST /api/leave-entitlements/forfeit (Parity-Gap A-3, Spec §3.7.3).

Stichtags-Verfall: REST wird je anspruchsverbundener Art auf den Verbrauch
bis einschließlich Stichtag gekürzt (nie erhöht); dry_run liefert nur die
Vorschau, ohne 5LEAEN zu schreiben.
"""

import pytest
from starlette.testclient import TestClient


def _entitled_leave_type_id(client: TestClient) -> int:
    types = client.get("/api/leave-types").json()
    for lt in types:
        if lt.get("ENTITLED"):
            return lt["ID"]
    pytest.skip("Keine anspruchsverbundene Abwesenheitsart in der Fixture-DB")


class TestLeaveForfeit:
    def _setup_entitlement(self, client: TestClient) -> tuple[int, int]:
        emp_id = client.get("/api/employees").json()[0]["ID"]
        lt_id = _entitled_leave_type_id(client)
        res = client.post(
            "/api/leave-entitlements",
            json={
                "employee_id": emp_id,
                "year": 2030,
                "days": 30.0,
                "carry_forward": 5.0,
                "leave_type_id": lt_id,
            },
        )
        assert res.status_code == 200
        return emp_id, lt_id

    def _rest(self, client: TestClient, emp_id: int, lt_id: int) -> float:
        rows = client.get(
            f"/api/leave-entitlements?year=2030&employee_id={emp_id}"
        ).json()
        return next(
            r["carry_forward"] for r in rows if r["leave_type_id"] == lt_id
        )

    def test_dry_run_previews_without_writing(self, write_client: TestClient):
        emp_id, lt_id = self._setup_entitlement(write_client)
        res = write_client.post(
            "/api/leave-entitlements/forfeit",
            json={"cutoff_date": "2030-03-31", "dry_run": True},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["dry_run"] is True
        cut = next(
            c
            for c in data["cuts"]
            if c["employee_id"] == emp_id and c["leave_type_id"] == lt_id
        )
        # Spec §3.7.3: REST (5,0) > Verbrauch bis Stichtag (0,0) → Kürzung auf 0
        assert cut["old_rest"] == pytest.approx(5.0)
        assert cut["new_rest"] == pytest.approx(0.0)
        # Vorschau schreibt nicht
        assert self._rest(write_client, emp_id, lt_id) == pytest.approx(5.0)

    def test_forfeit_writes_rest_cut(self, write_client: TestClient):
        emp_id, lt_id = self._setup_entitlement(write_client)
        res = write_client.post(
            "/api/leave-entitlements/forfeit",
            json={"cutoff_date": "2030-03-31"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["dry_run"] is False
        assert data["total_forfeited"] >= 5.0
        # Spec §3.7.3: REST gekürzt auf Verbrauch (0), ENTITLEMNT unberührt
        rows = write_client.get(
            f"/api/leave-entitlements?year=2030&employee_id={emp_id}"
        ).json()
        row = next(r for r in rows if r["leave_type_id"] == lt_id)
        assert row["carry_forward"] == pytest.approx(0.0)
        assert row["entitlement"] == pytest.approx(30.0)

        # Idempotent: zweiter Lauf kürzt für diesen MA nichts mehr
        res2 = write_client.post(
            "/api/leave-entitlements/forfeit",
            json={"cutoff_date": "2030-03-31"},
        )
        assert res2.status_code == 200
        assert not any(
            c["employee_id"] == emp_id and c["leave_type_id"] == lt_id
            for c in res2.json()["cuts"]
        )

    def test_invalid_date_rejected(self, write_client: TestClient):
        res = write_client.post(
            "/api/leave-entitlements/forfeit",
            json={"cutoff_date": "2030-02-30"},
        )
        assert res.status_code == 422

    def test_requires_admin(self, planer_client: TestClient):
        res = planer_client.post(
            "/api/leave-entitlements/forfeit",
            json={"cutoff_date": "2030-03-31"},
        )
        assert res.status_code == 403
