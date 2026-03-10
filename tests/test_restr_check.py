"""
Test: POST /api/schedule respects RESTR restrictions.

The individual schedule entry endpoint should return HTTP 409 with
"restriction" in the detail when a RESTR entry blocks the assignment.
"""

import pytest


class TestRestrictionCheck:
    """Verify that RESTR entries block POST /api/schedule with HTTP 409."""

    def _get_emp_shift(self, db_path):
        from sp5lib.database import SP5Database

        db = SP5Database(db_path)
        employees = db.get_employees()
        shifts = db.get_shifts()
        if not employees or not shifts:
            pytest.skip("No employees or shifts in fixture DB")
        return employees[0]["ID"], shifts[0]["ID"]

    def test_no_restriction_allows_entry(self, planer_client, write_db_path):
        """Without a restriction, scheduling succeeds (no restriction 409)."""
        emp_id, shift_id = self._get_emp_shift(write_db_path)
        resp = planer_client.post(
            "/api/schedule",
            json={
                "employee_id": emp_id,
                "shift_id": shift_id,
                "date": "2099-01-05",  # Monday
            },
        )
        # May succeed or fail for other reasons — but NOT due to restrictions
        if resp.status_code == 409:
            assert "restriction" not in resp.json().get("detail", "").lower()

    def test_restriction_all_days_blocks(self, planer_client, write_db_path):
        """RESTR weekday=0 (all days) → POST returns 409 with restriction."""
        from sp5lib.database import SP5Database

        emp_id, shift_id = self._get_emp_shift(write_db_path)

        # Insert restriction directly into the DB copy
        db = SP5Database(write_db_path)
        db.set_restriction(
            employee_id=emp_id, shift_id=shift_id, reason="Test-Sperre", weekday=0
        )

        resp = planer_client.post(
            "/api/schedule",
            json={
                "employee_id": emp_id,
                "shift_id": shift_id,
                "date": "2099-01-07",
            },
        )
        assert resp.status_code == 409, (
            f"Expected 409, got {resp.status_code}: {resp.text}"
        )
        assert "restriction" in resp.json().get("detail", "").lower()

    def test_restriction_weekday_match_blocks(self, planer_client, write_db_path):
        """RESTR weekday=5 (Friday) blocks assignment on a Friday."""
        from sp5lib.database import SP5Database

        emp_id, shift_id = self._get_emp_shift(write_db_path)

        db = SP5Database(write_db_path)
        db.set_restriction(
            employee_id=emp_id, shift_id=shift_id, reason="Nur Mo-Do", weekday=5
        )

        # 2099-01-09 is a Friday (isoweekday=5)
        resp = planer_client.post(
            "/api/schedule",
            json={
                "employee_id": emp_id,
                "shift_id": shift_id,
                "date": "2099-01-09",
            },
        )
        assert resp.status_code == 409, (
            f"Expected 409, got {resp.status_code}: {resp.text}"
        )
        assert "restriction" in resp.json().get("detail", "").lower()

    def test_restriction_weekday_no_match_allows(self, planer_client, write_db_path):
        """RESTR weekday=5 (Friday) does NOT block on a Monday."""
        from sp5lib.database import SP5Database

        emp_id, shift_id = self._get_emp_shift(write_db_path)

        db = SP5Database(write_db_path)
        db.set_restriction(
            employee_id=emp_id, shift_id=shift_id, reason="Nur Mo-Do", weekday=5
        )

        # 2099-01-05 is a Monday (isoweekday=1) — restriction is for Friday only
        resp = planer_client.post(
            "/api/schedule",
            json={
                "employee_id": emp_id,
                "shift_id": shift_id,
                "date": "2099-01-05",
            },
        )
        if resp.status_code == 409:
            assert "restriction" not in resp.json().get("detail", "").lower()
