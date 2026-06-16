"""Tests für die kleinen Paritätslücken C-5/C-6/C-8.

C-5: /api/statistics/shifts zählt über die Fassade (inkl. 5CYASS-Zyklen).
C-6: /api/leave-balance liefert die Aufschlüsselung je Art (by_type).
C-8: /api/extracharges/summary mit freiem Auswertungszeitraum from/to.
"""

from datetime import date

import pytest
from starlette.testclient import TestClient


class TestShiftStatisticsCycles:
    def test_shift_statistics_include_cycle_duties(self, write_client: TestClient):
        """Spec §3.9.3 Nr. 4 (C-5): Zyklusdienste zählen in der Schichtstatistik."""
        today = date.today()
        emp_id = write_client.get("/api/employees").json()[0]["ID"]
        shift_id = write_client.get("/api/shifts").json()[0]["ID"]

        before = write_client.get(
            f"/api/statistics/shifts?year={today.year}&months=1"
        ).json()
        before_total = sum(s["total"] for s in before["shift_usage"])

        res = write_client.post(
            "/api/shift-cycles", json={"name": "C5-Zyklus", "size_weeks": 1}
        )
        assert res.status_code == 200
        cycle_id = res.json()["cycle"]["ID"]
        res = write_client.put(
            f"/api/shift-cycles/{cycle_id}",
            json={
                "name": "C5-Zyklus",
                "size_weeks": 1,
                "entries": [{"index": i, "shift_id": shift_id} for i in range(7)],
            },
        )
        assert res.status_code == 200
        res = write_client.post(
            "/api/shift-cycles/assign",
            json={
                "employee_id": emp_id,
                "cycle_id": cycle_id,
                "start_date": today.replace(day=1).isoformat(),
            },
        )
        assert res.status_code == 200

        after = write_client.get(
            f"/api/statistics/shifts?year={today.year}&months=1"
        ).json()
        after_total = sum(s["total"] for s in after["shift_usage"])
        assert after_total > before_total, (
            "Zyklusdienste fehlen in /api/statistics/shifts (Gap C-5)"
        )
        dist = next(
            (d for d in after["employee_distribution"] if d["employee_id"] == emp_id),
            None,
        )
        assert dist is not None and dist["total_shifts"] >= 28


class TestLeaveBalanceByType:
    def test_leave_balance_has_per_type_breakdown(self, write_client: TestClient):
        """Spec §3.9.3 Nr. 6 (C-6): Doppelwert je Art zusätzlich zur Summe."""
        emp_id = write_client.get("/api/employees").json()[0]["ID"]
        lt = next(
            t
            for t in write_client.get("/api/leave-types").json()
            if t.get("ENTITLED")
        )
        res = write_client.post(
            "/api/leave-entitlements",
            json={
                "employee_id": emp_id,
                "year": 2031,
                "days": 25.0,
                "carry_forward": 3.0,
                "leave_type_id": lt["ID"],
            },
        )
        assert res.status_code == 200

        bal = write_client.get(
            f"/api/leave-balance?year=2031&employee_id={emp_id}"
        ).json()
        assert "by_type" in bal
        row = next(t for t in bal["by_type"] if t["leave_type_id"] == lt["ID"])
        assert row["entitlement"] == pytest.approx(25.0)
        assert row["carry_forward"] == pytest.approx(3.0)
        assert row["total"] == pytest.approx(28.0)
        assert row["remaining"] == pytest.approx(28.0 - row["used"])
        # Summe == Summe der Arten
        assert bal["entitlement"] == pytest.approx(
            sum(t["entitlement"] for t in bal["by_type"])
        )


class TestExtrachargeSummaryPeriod:
    def test_period_equals_month(self, sync_client: TestClient):
        """Spec §3.9.1 (C-8): from/to über einen Monat == year/month."""
        by_month = sync_client.get(
            "/api/extracharges/summary?year=2026&month=1"
        ).json()
        by_period = sync_client.get(
            "/api/extracharges/summary?from=2026-01-01&to=2026-01-31"
        ).json()
        assert by_period == by_month

    def test_partial_period(self, sync_client: TestClient):
        full = sync_client.get(
            "/api/extracharges/summary?from=2026-01-01&to=2026-01-31"
        ).json()
        part = sync_client.get(
            "/api/extracharges/summary?from=2026-01-01&to=2026-01-07"
        ).json()
        full_h = {r["charge_id"]: r["hours"] for r in full}
        for r in part:
            assert r["hours"] <= full_h[r["charge_id"]] + 0.01

    def test_validation(self, sync_client: TestClient):
        assert (
            sync_client.get("/api/extracharges/summary?from=2026-01-01").status_code
            == 400
        )
        assert (
            sync_client.get(
                "/api/extracharges/summary?from=2026-02-01&to=2026-01-31"
            ).status_code
            == 400
        )
        assert sync_client.get("/api/extracharges/summary").status_code == 400
        assert (
            sync_client.get("/api/extracharges/summary?year=2026").status_code == 400
        )


class TestExtrachargeByDay:
    """A8 Zeitzuschläge je Tag: /api/extracharges/by-day."""

    def test_rows_shape_and_period(self, sync_client: TestClient):
        by_month = sync_client.get("/api/extracharges/by-day?year=2026&month=1")
        assert by_month.status_code == 200
        rows = by_month.json()
        assert isinstance(rows, list)
        for r in rows:
            assert {"employee_id", "date", "charge_id", "charge_name", "hours"} <= set(r)
            assert r["date"].startswith("2026-01") and r["hours"] > 0
        # freier Zeitraum über den Monat == year/month
        period = sync_client.get(
            "/api/extracharges/by-day?from=2026-01-01&to=2026-01-31"
        ).json()
        assert period == rows

    def test_daily_sums_match_summary(self, sync_client: TestClient):
        """Invariante: Summe der Tageszeilen je Regel == aggregierter Summenwert."""
        rows = sync_client.get("/api/extracharges/by-day?year=2026&month=1").json()
        summary = sync_client.get("/api/extracharges/summary?year=2026&month=1").json()
        per_charge: dict[int, float] = {}
        for r in rows:
            per_charge[r["charge_id"]] = per_charge.get(r["charge_id"], 0.0) + r["hours"]
        for s in summary:
            assert per_charge.get(s["charge_id"], 0.0) == pytest.approx(s["hours"], abs=0.05)

    def test_validation(self, sync_client: TestClient):
        assert sync_client.get("/api/extracharges/by-day?from=2026-01-01").status_code == 400
        assert (
            sync_client.get("/api/extracharges/by-day?from=2026-02-01&to=2026-01-31").status_code
            == 400
        )
        assert sync_client.get("/api/extracharges/by-day").status_code == 400
        assert sync_client.get("/api/extracharges/by-day?year=2026").status_code == 400
