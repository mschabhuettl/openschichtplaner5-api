"""Tests for Q030: Improved conflict detection on shift assignment.

Tests:
  1. Duplicate assignment (same employee, same shift, same date) → 409
  2. Overlapping shifts (time-based) → 409
  3. Absence/vacation conflict → 409
  4. Normal assignment still works when no conflicts
  5. Helper functions (_parse_time_range, _times_overlap)
"""

import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ── Unit tests for helper functions ──────────────────────────────────────────

class TestTimeHelpers:
    """Unit tests for time parsing and overlap detection helpers."""

    def test_parse_time_range_valid(self):
        from api.routers.schedule import _parse_time_range
        assert _parse_time_range("06:00-14:00") == (360, 840)
        assert _parse_time_range("14:00-22:00") == (840, 1320)

    def test_parse_time_range_overnight(self):
        from api.routers.schedule import _parse_time_range
        # 22:00-06:00 = overnight → end gets +24h
        result = _parse_time_range("22:00-06:00")
        assert result == (1320, 1800)

    def test_parse_time_range_invalid(self):
        from api.routers.schedule import _parse_time_range
        assert _parse_time_range("") is None
        assert _parse_time_range(None) is None
        assert _parse_time_range("invalid") is None
        assert _parse_time_range("12:00") is None

    def test_times_overlap_yes(self):
        from api.routers.schedule import _times_overlap
        # 06:00-14:00 overlaps with 10:00-18:00
        assert _times_overlap((360, 840), (600, 1080)) is True

    def test_times_overlap_no(self):
        from api.routers.schedule import _times_overlap
        # 06:00-14:00 does NOT overlap with 14:00-22:00 (touching = no overlap)
        assert _times_overlap((360, 840), (840, 1320)) is False

    def test_times_overlap_none(self):
        from api.routers.schedule import _times_overlap
        assert _times_overlap(None, (360, 840)) is False
        assert _times_overlap((360, 840), None) is False


# ── Integration tests ────────────────────────────────────────────────────────

class TestConflictDetectionAPI:
    """Integration tests for conflict detection in POST /api/schedule."""

    def _find_valid_employee_and_shift(self, client):
        """Find a valid employee ID and shift ID from the real DB."""
        resp = client.get("/api/employees")
        assert resp.status_code == 200
        employees = resp.json()
        assert len(employees) > 0
        emp_id = employees[0]["ID"]

        resp = client.get("/api/shifts")
        assert resp.status_code == 200
        shifts = resp.json()
        assert len(shifts) > 0
        shift = shifts[0]
        return emp_id, shift

    def test_normal_assignment_works(self, write_client):
        """A normal assignment with no conflicts should succeed."""
        emp_id, shift = self._find_valid_employee_and_shift(write_client)
        # Use a future date unlikely to have existing entries
        date = "2099-01-15"
        # Clean up first
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

        resp = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": shift["ID"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

    def test_duplicate_assignment_returns_409(self, write_client):
        """Assigning the same shift to the same employee on the same date → 409."""
        emp_id, shift = self._find_valid_employee_and_shift(write_client)
        date = "2099-02-10"
        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

        # First assignment should succeed
        resp1 = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": shift["ID"],
        })
        assert resp1.status_code == 200

        # Second identical assignment should fail with 409
        resp2 = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": shift["ID"],
        })
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        if isinstance(detail, dict):
            assert detail["type"] == "duplicate_assignment"
        # String detail also acceptable (from add_schedule_entry ValueError)

        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

    def test_absence_conflict_returns_409(self, write_client):
        """Assigning a shift when employee has an absence → 409."""
        emp_id, shift = self._find_valid_employee_and_shift(write_client)
        date = "2099-03-05"
        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

        # Get a leave type
        resp = write_client.get("/api/leave-types")
        assert resp.status_code == 200
        leave_types = resp.json()
        if not leave_types:
            pytest.skip("No leave types in test DB")
        leave_type_id = leave_types[0]["ID"]

        # Create an absence
        resp_abs = write_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": date,
            "leave_type_id": leave_type_id,
        })
        assert resp_abs.status_code == 200

        # Now try to assign a shift → should get 409
        resp_shift = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": shift["ID"],
        })
        assert resp_shift.status_code == 409
        detail = resp_shift.json()["detail"]
        if isinstance(detail, dict):
            assert detail["type"] == "absence_conflict"
            assert "absence" in detail["message"].lower()

        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

    def test_overlapping_shift_returns_409(self, write_client):
        """Assigning a shift that overlaps in time with an existing one → 409."""
        emp_id, shift = self._find_valid_employee_and_shift(write_client)

        # Find two shifts with overlapping times
        resp = write_client.get("/api/shifts")
        shifts = resp.json()

        # Find shifts with actual time data
        shifts_with_times = []
        for s in shifts:
            startend0 = (s.get("STARTEND0", "") or "").strip()
            if startend0 and "-" in startend0:
                shifts_with_times.append(s)

        if len(shifts_with_times) < 2:
            pytest.skip("Need at least 2 shifts with time data for overlap test")

        # Try to find two shifts that actually overlap
        from api.routers.schedule import _parse_time_range, _times_overlap
        overlapping_pair = None
        for i, s1 in enumerate(shifts_with_times):
            r1 = _parse_time_range(s1.get("STARTEND0", ""))
            for s2 in shifts_with_times[i + 1:]:
                r2 = _parse_time_range(s2.get("STARTEND0", ""))
                if _times_overlap(r1, r2):
                    overlapping_pair = (s1, s2)
                    break
            if overlapping_pair:
                break

        if not overlapping_pair:
            pytest.skip("No overlapping shift pair found in test DB")

        s1, s2 = overlapping_pair
        date = "2099-04-07"  # Monday
        write_client.delete(f"/api/schedule/{emp_id}/{date}")

        # Assign first shift
        resp1 = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": s1["ID"],
        })
        assert resp1.status_code == 200

        # Assign overlapping shift → should get 409
        resp2 = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": date,
            "shift_id": s2["ID"],
        })
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        if isinstance(detail, dict):
            assert detail["type"] == "overlapping_shift"
            assert "overlap" in detail["message"].lower()

        # Clean up
        write_client.delete(f"/api/schedule/{emp_id}/{date}")
