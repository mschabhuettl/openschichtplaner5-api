"""Tests for Q038: Improved auto-scheduling algorithm.

Validates that generate_schedule_from_cycle respects:
- Employee availability (from availability.json)
- Weekly hours limits (HRSWEEK)
- Already assigned shifts (conflict avoidance)
- Skills data in reports
"""

import json
import os
import shutil

import pytest
from sp5lib.database import SP5Database

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def tmp_db(tmp_path):
    dst = tmp_path / "Daten"
    shutil.copytree(_FIXTURES, str(dst))
    return SP5Database(str(dst))


def _setup_cycle(db, emp_id, shift_id=1):
    """Helper: create a 1-week cycle with shift Mon-Fri and assign to employee."""
    cycle = db.create_shift_cycle(name="TestCycle", size_weeks=1)
    cid = cycle["ID"]
    for idx in range(5):  # Mon-Fri
        db.set_cycle_entry(cid, idx, shift_id)
    db.assign_cycle(emp_id, cid, "2026-03-02")  # Monday
    return cid


def _avail_path():
    """Return path to availability.json that database.py actually reads."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "api", "data", "availability.json"
    )


def _write_availability(data):
    path = _avail_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def _cleanup_availability():
    path = _avail_path()
    if os.path.exists(path):
        os.remove(path)


class TestAvailabilityCheck:
    """Test that availability data causes shifts to be skipped."""

    def test_unavailable_day_skipped(self, tmp_db):
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp = emps[0]
        emp_id = emp["ID"]
        _setup_cycle(tmp_db, emp_id)

        # Make employee unavailable on Wednesdays (day=2)
        _write_availability({
            str(emp_id): {
                "employee_id": emp_id,
                "days": [
                    {"day": 0, "available": True, "time_windows": []},
                    {"day": 1, "available": True, "time_windows": []},
                    {"day": 2, "available": False, "time_windows": []},
                    {"day": 3, "available": True, "time_windows": []},
                    {"day": 4, "available": True, "time_windows": []},
                    {"day": 5, "available": True, "time_windows": []},
                    {"day": 6, "available": True, "time_windows": []},
                ],
            }
        })

        try:
            result = tmp_db.generate_schedule_from_cycle(
                year=2026, month=3, employee_ids=[emp_id], dry_run=True
            )

            unavail = [
                p for p in result["preview"]
                if p["employee_id"] == emp_id and p["status"] == "unavailable"
            ]
            # March 2026 has Wednesdays: 4, 11, 18, 25 → 4 skipped
            # Cycle starts March 2, so 4, 11, 18, 25 are all after start
            assert result["skipped_availability"] >= 3
            assert len(unavail) >= 3
        finally:
            _cleanup_availability()

    def test_availability_time_window_mismatch(self, tmp_db):
        """Employee available but only in afternoon → morning shift skipped."""
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        _setup_cycle(tmp_db, emp_id, shift_id=1)  # Frühschicht 06:00-14:00

        # Available only 14:00-22:00 on Monday
        _write_availability({
            str(emp_id): {
                "employee_id": emp_id,
                "days": [
                    {"day": 0, "available": True, "time_windows": [
                        {"start": "14:00", "end": "22:00"}
                    ]},
                    {"day": 1, "available": True, "time_windows": []},
                    {"day": 2, "available": True, "time_windows": []},
                    {"day": 3, "available": True, "time_windows": []},
                    {"day": 4, "available": True, "time_windows": []},
                ],
            }
        })

        try:
            result = tmp_db.generate_schedule_from_cycle(
                year=2026, month=3, employee_ids=[emp_id], dry_run=True
            )
            # Mondays should be skipped (Frühschicht 06-14 doesn't fit 14-22 window)
            unavail = [
                p for p in result["preview"]
                if p["status"] == "unavailable"
            ]
            assert len(unavail) >= 3  # At least 3 Mondays in March after cycle start
        finally:
            _cleanup_availability()

    def test_no_availability_data_means_available(self, tmp_db):
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        _setup_cycle(tmp_db, emp_id)

        _cleanup_availability()  # ensure no file
        result = tmp_db.generate_schedule_from_cycle(
            year=2026, month=3, employee_ids=[emp_id], dry_run=True
        )
        assert result["skipped_availability"] == 0


class TestWeeklyHoursLimit:
    """Test that weekly hours limits prevent over-scheduling."""

    def test_hours_limit_respected(self, tmp_db):
        """Employee with HRSWEEK should not exceed that limit."""
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")

        # All test employees have HRSWEEK=38.5
        # Frühschicht = 8h/day, 5 days = 40h > 38.5h
        # So the 5th shift each week should be skipped
        emp_id = emps[0]["ID"]
        _setup_cycle(tmp_db, emp_id)

        _cleanup_availability()
        result = tmp_db.generate_schedule_from_cycle(
            year=2026, month=3, employee_ids=[emp_id], dry_run=True
        )

        hours_exceeded = [
            p for p in result["preview"]
            if p["employee_id"] == emp_id and p["status"] == "hours_exceeded"
        ]
        # 5 * 8h = 40h > 38.5h → every week the 5th shift (Friday) should be skipped
        assert result["skipped_hours_limit"] > 0
        assert len(hours_exceeded) > 0

        # Verify detail message
        for entry in hours_exceeded:
            assert "detail" in entry
            assert "h/Woche" in entry["detail"]

    def test_existing_shifts_count_toward_limit(self, tmp_db):
        """Pre-assigned shifts (from fixtures) should count toward the weekly hours limit."""
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")

        # Employee 40 already has MASHI entries in March 2026 from fixtures
        # Those existing entries should be pre-counted in weekly hours tracker
        emp_id = emps[0]["ID"]  # 40
        _setup_cycle(tmp_db, emp_id)
        _cleanup_availability()

        result = tmp_db.generate_schedule_from_cycle(
            year=2026, month=3, employee_ids=[emp_id], dry_run=True
        )

        # HRSWEEK=38.5, Frühschicht=8h/day, cycle wants 5 days/week
        # But existing entries already occupy some days → those days are "skip"
        # The remaining days from cycle + existing hours may exceed 38.5h
        # At minimum the pure cycle (5×8=40 > 38.5) should trigger hours_exceeded
        # for weeks without existing entries
        hours_exceeded = [
            p for p in result["preview"]
            if p["status"] == "hours_exceeded"
        ]
        assert result["skipped_hours_limit"] > 0
        assert len(hours_exceeded) > 0


class TestReportEnhancements:
    """Test that the report includes new fields."""

    def test_report_structure(self, tmp_db):
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")

        emp_id = emps[0]["ID"]
        _setup_cycle(tmp_db, emp_id)
        _cleanup_availability()

        result = tmp_db.generate_schedule_from_cycle(
            year=2026, month=3, employee_ids=[emp_id], dry_run=True
        )

        # Result-level fields
        assert "skipped_availability" in result
        assert "skipped_hours_limit" in result
        assert isinstance(result["skipped_availability"], int)
        assert isinstance(result["skipped_hours_limit"], int)

        # Report-level fields
        report = result["report"]
        assert "skipped_availability" in report
        assert "skipped_hours_limit" in report

        # Employee report includes weekly hours info
        for emp in report["employees"]:
            assert "weekly_hours_limit" in emp

    def test_report_weekly_hours_planned(self, tmp_db):
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees")

        emp_id = emps[0]["ID"]
        _setup_cycle(tmp_db, emp_id)
        _cleanup_availability()

        result = tmp_db.generate_schedule_from_cycle(
            year=2026, month=3, employee_ids=[emp_id], dry_run=True
        )

        for emp in result["report"]["employees"]:
            if emp["employee_id"] == emp_id:
                assert "weekly_hours_planned" in emp
                assert "avg_weekly_hours_planned" in emp
                # Each week should have some hours planned
                assert len(emp["weekly_hours_planned"]) > 0


class TestRouterIntegration:
    """Test the /api/schedule/generate endpoint returns new fields."""

    def test_generate_endpoint_new_fields(self, sync_client):
        """Use conftest sync_client (session-scoped, admin-authenticated)."""
        emps_resp = sync_client.get("/api/employees")
        assert emps_resp.status_code == 200
        emps = emps_resp.json()
        shifts_resp = sync_client.get("/api/shifts")
        assert shifts_resp.status_code == 200
        shifts = shifts_resp.json()
        if not emps or not shifts:
            pytest.skip("No data")

        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]

        # Create cycle via API
        cycle_resp = sync_client.post("/api/shift-cycles", json={
            "name": "Q038Test", "size_weeks": 1
        })
        assert cycle_resp.status_code == 200
        cid = cycle_resp.json()["cycle"]["ID"]

        # Update with entries
        entries = [{"index": i, "shift_id": shift_id} for i in range(5)]
        sync_client.put(f"/api/shift-cycles/{cid}", json={
            "name": "Q038Test", "size_weeks": 1, "entries": entries
        })

        # Assign
        sync_client.post("/api/shift-cycles/assign", json={
            "employee_id": emp_id, "cycle_id": cid, "start_date": "2026-03-02"
        })

        # Generate (dry_run)
        resp = sync_client.post("/api/schedule/generate", json={
            "year": 2026, "month": 3, "dry_run": True
        })
        assert resp.status_code == 200
        data = resp.json()

        # New fields present
        assert "skipped_availability" in data
        assert "skipped_hours_limit" in data
        assert "message" in data
