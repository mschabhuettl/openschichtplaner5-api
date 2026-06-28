"""Tests für expandierte 5CYASS-Zyklusdienste im Plan-Lesepfad
(Parity-Gap B-2, Spec §6.3/§4.2).

Unmaterialisierte Zyklusdienste erscheinen in /api/schedule,
/api/schedule/day und /api/schedule/week als generierte Einträge mit
source='cycle'; materialisierte 5MASHI-Tage gewinnen.
"""

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def cycle_setup(write_client: TestClient):
    """1-Wochen-Zyklus (Mo-Fr Schicht) für einen Mitarbeiter ab 2027-06-07 (Mo)."""
    emp_id = write_client.get("/api/employees").json()[0]["ID"]
    shift_id = write_client.get("/api/shifts").json()[0]["ID"]

    res = write_client.post(
        "/api/shift-cycles", json={"name": "B2-Testzyklus", "size_weeks": 1}
    )
    assert res.status_code == 200
    cycle_id = res.json()["cycle"]["ID"]
    res = write_client.put(
        f"/api/shift-cycles/{cycle_id}",
        json={
            "name": "B2-Testzyklus",
            "size_weeks": 1,
            "entries": [{"index": i, "shift_id": shift_id} for i in range(5)],
        },
    )
    assert res.status_code == 200
    res = write_client.post(
        "/api/shift-cycles/assign",
        json={"employee_id": emp_id, "cycle_id": cycle_id, "start_date": "2027-06-07"},
    )
    assert res.status_code == 200
    return emp_id, shift_id


class TestCycleExpansionInScheduleReads:
    def test_month_schedule_contains_cycle_entries(
        self, write_client: TestClient, cycle_setup
    ):
        emp_id, shift_id = cycle_setup
        entries = write_client.get("/api/schedule?year=2027&month=6").json()
        cycle_entries = [
            e
            for e in entries
            if e["employee_id"] == emp_id and e.get("source") == "cycle"
        ]
        assert cycle_entries, "Zyklusdienste fehlen im Monatsplan (Gap B-2)"
        assert {e["shift_id"] for e in cycle_entries} == {shift_id}
        # Mo 7.6. bis Fr 11.6. aus dem Zyklus, Sa/So frei
        dates = {e["date"] for e in cycle_entries}
        assert {"2027-06-07", "2027-06-11"}.issubset(dates)
        assert "2027-06-12" not in dates

    def test_materialized_day_wins(self, write_client: TestClient, cycle_setup):
        """5MASHI-Tage gewinnen — kein Zyklus-Duplikat am materialisierten Tag."""
        emp_id, shift_id = cycle_setup
        res = write_client.post(
            "/api/schedule",
            json={"employee_id": emp_id, "date": "2027-06-08", "shift_id": shift_id},
        )
        assert res.status_code == 200, res.text
        entries = write_client.get("/api/schedule?year=2027&month=6").json()
        day_entries = [
            e
            for e in entries
            if e["employee_id"] == emp_id
            and e["date"] == "2027-06-08"
            and e["kind"] == "shift"
        ]
        assert len(day_entries) == 1
        assert day_entries[0].get("source") is None

    def test_day_and_week_views_show_cycle(
        self, write_client: TestClient, cycle_setup
    ):
        emp_id, shift_id = cycle_setup
        day = write_client.get("/api/schedule/day?date=2027-06-09").json()
        row = next(r for r in day if r["employee_id"] == emp_id)
        assert row["kind"] == "shift"
        assert row["source"] == "cycle"
        assert row["shift_id"] == shift_id

        week = write_client.get("/api/schedule/week?date=2027-06-07").json()
        wed = next(d for d in week["days"] if d["date"] == "2027-06-09")
        row = next(r for r in wed["entries"] if r["employee_id"] == emp_id)
        assert row["source"] == "cycle"
        assert row["shift_id"] == shift_id


class TestCycleExceptionSuppressesDuty:
    """5CYEXC = freier Tag: eine Ausnahme streicht den generierten Zyklusdienst.

    Der Frontend-Body trägt KEIN `type` (5CYEXC hat kein Ersatzschicht-Feld;
    `type` ist die Plan-Eintragsart, Field(ge=0, le=1)). Früher schickte das
    Frontend `type: shiftId` → 422; dieser Round-Trip sichert den Bugfix ab.
    """

    def test_exception_without_type_removes_cycle_entry(
        self, write_client: TestClient, cycle_setup
    ):
        emp_id, _shift_id = cycle_setup
        ass = next(
            a
            for a in write_client.get("/api/shift-cycles/assign").json()
            if a["employee_id"] == emp_id
        )
        # Genau der Body, den das Frontend jetzt sendet — ohne `type`.
        res = write_client.post(
            "/api/cycle-exceptions",
            json={
                "employee_id": emp_id,
                "cycle_assignment_id": ass["id"],
                "date": "2027-06-09",
            },
        )
        assert res.status_code == 200, res.text

        entries = write_client.get("/api/schedule?year=2027&month=6").json()
        wed = [
            e
            for e in entries
            if e["employee_id"] == emp_id and e["date"] == "2027-06-09"
        ]
        assert all(e.get("source") != "cycle" for e in wed), (
            "Zyklusdienst trotz Ausnahme noch vorhanden — Ausnahme wirkt nicht"
        )
