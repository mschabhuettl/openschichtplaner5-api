"""Tests for overtime/underhours tracking endpoints (Q068).

Covers:
  - GET /api/v1/employees/{id}/overtime?year=YYYY&month=MM
  - GET /api/v1/overtime/summary?year=YYYY&month=MM&group_id=X
  - Auth guards (unauthenticated → 401)
  - Employees with no shifts
  - Edge case: no contract hours (HRSWEEK=0)
  - Edge case: leap-year February (2024-02)
  - Group summary filtering
"""

import calendar
import secrets
from datetime import datetime

import pytest
from starlette.testclient import TestClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def _count_workdays_mon_fri(year: int, month: int) -> int:
    num_days = calendar.monthrange(year, month)[1]
    return sum(1 for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5)


def _planer_client(app):
    """Return a TestClient with a Planer token injected."""
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 800,
        "NAME": "test_planer_overtime",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    client = TestClient(app, raise_server_exceptions=True)
    client.headers["X-Auth-Token"] = tok
    return client, tok


# ── Tests: single employee ────────────────────────────────────────────────────


class TestEmployeeOvertime:
    def test_unauthenticated_returns_401(self, app):
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/api/v1/employees/1/overtime?year=2024&month=1")
        assert res.status_code == 401

    def test_returns_200_for_existing_employee(self, sync_client: TestClient):
        """Overtime endpoint returns 200 for a real employee."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=1")
        assert r.status_code == 200

    def test_response_has_required_fields(self, sync_client: TestClient):
        """Overtime response contains all required fields."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=3")
        assert r.status_code == 200
        data = r.json()
        for field in ("contract_hours", "expected_hours", "actual_hours", "difference", "shifts_count"):
            assert field in data, f"Missing field: {field}"
        assert data["employee_id"] == emp_id
        assert data["year"] == 2024
        assert data["month"] == 3

    def test_difference_is_actual_minus_expected(self, sync_client: TestClient):
        """difference == actual_hours - expected_hours."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=6")
        assert r.status_code == 200
        d = r.json()
        expected_diff = round(d["actual_hours"] - d["expected_hours"], 2)
        assert abs(d["difference"] - expected_diff) < 0.01, (
            f"difference mismatch: {d['difference']} vs {expected_diff}"
        )

    def test_expected_hours_formula(self, sync_client: TestClient):
        """expected_hours == contract_hours * working_days_in_month / 5."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        year, month = 2024, 4  # April 2024: 22 working days
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year={year}&month={month}")
        assert r.status_code == 200
        d = r.json()
        working_days = _count_workdays_mon_fri(year, month)
        expected = round(d["contract_hours"] * working_days / 5, 2) if d["contract_hours"] else 0.0
        assert abs(d["expected_hours"] - expected) < 0.01, (
            f"expected_hours mismatch: {d['expected_hours']} vs {expected}"
        )

    def test_employee_with_no_shifts_has_zero_actual(self, sync_client: TestClient):
        """Employee with no shifts in a future month has 0 actual_hours, 0 shifts_count."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        # Use a far-future month that will certainly have no shifts
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year=2099&month=1")
        assert r.status_code == 200
        d = r.json()
        assert d["actual_hours"] == 0.0
        assert d["shifts_count"] == 0

    def test_no_contract_hours_expected_is_zero(self, app):
        """Employee with HRSWEEK=0 → expected_hours=0, difference=actual_hours."""
        import unittest.mock as mock

        from api.dependencies import get_db as _get_db
        from api.main import _sessions

        db = _get_db()
        employees = db.get_employees()
        if not employees:
            pytest.skip("No employees in DB")

        emp = employees[0]
        emp_id = emp["ID"]
        patched_emp = {**emp, "HRSWEEK": 0}

        tok = secrets.token_hex(20)
        _sessions[tok] = {"ID": 803, "NAME": "test_admin_no_hrs", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        try:
            # Patch the SP5Database class method directly so the ASGI handler picks it up
            with mock.patch.object(type(db), "get_employee", return_value=patched_emp):
                r = client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=1")
        finally:
            _sessions.pop(tok, None)
        assert r.status_code == 200
        d = r.json()
        assert d["contract_hours"] == 0.0
        assert d["expected_hours"] == 0.0
        # difference == actual (since expected == 0)
        assert abs(d["difference"] - d["actual_hours"]) < 0.01

    def test_leap_year_february(self, sync_client: TestClient):
        """Feb 2024 (leap year, 29 days) — expected_hours calculated correctly."""
        res = sync_client.get("/api/v1/employees")
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]["ID"]
        # 2024-02: leap year Feb has 29 days, 21 working days (Mon-Fri)
        r = sync_client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=2")
        assert r.status_code == 200
        d = r.json()
        working_days = _count_workdays_mon_fri(2024, 2)
        assert working_days == 21  # Sanity-check the fixture
        expected = round(d["contract_hours"] * 21 / 5, 2) if d["contract_hours"] else 0.0
        assert abs(d["expected_hours"] - expected) < 0.01

    def test_nonexistent_employee_returns_404(self, sync_client: TestClient):
        """Overtime endpoint returns 404 for a non-existent employee."""
        r = sync_client.get("/api/v1/employees/999999/overtime?year=2024&month=1")
        assert r.status_code == 404

    def test_planer_can_access(self, app):
        """Planer role can access the overtime endpoint."""
        from api.main import _sessions

        client, tok = _planer_client(app)
        try:
            res = client.get("/api/v1/employees")
            employees = res.json()
            if not employees:
                pytest.skip("No employees in DB")
            emp_id = employees[0]["ID"]
            r = client.get(f"/api/v1/employees/{emp_id}/overtime?year=2024&month=1")
            assert r.status_code == 200
        finally:
            _sessions.pop(tok, None)


# ── Tests: summary ─────────────────────────────────────────────────────────────


class TestOvertimeSummary:
    def test_unauthenticated_returns_401(self, app):
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/api/v1/overtime/summary?year=2024&month=1")
        assert res.status_code == 401

    def test_summary_returns_200(self, sync_client: TestClient):
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=1")
        assert r.status_code == 200

    def test_summary_has_required_fields(self, sync_client: TestClient):
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=3")
        assert r.status_code == 200
        data = r.json()
        assert "year" in data
        assert "month" in data
        assert "count" in data
        assert "employees" in data
        assert isinstance(data["employees"], list)
        assert data["year"] == 2024
        assert data["month"] == 3

    def test_summary_sorted_by_difference_desc(self, sync_client: TestClient):
        """Summary is sorted by difference descending (most overtime first)."""
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=6")
        assert r.status_code == 200
        employees = r.json()["employees"]
        if len(employees) < 2:
            pytest.skip("Need at least 2 employees to test sorting")
        diffs = [e["difference"] for e in employees]
        assert diffs == sorted(diffs, reverse=True), "Employees not sorted by difference descending"

    def test_summary_employee_fields(self, sync_client: TestClient):
        """Each employee in summary has required fields."""
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=1")
        assert r.status_code == 200
        employees = r.json()["employees"]
        if not employees:
            pytest.skip("No employees in DB")
        e = employees[0]
        for field in ("employee_id", "employee_name", "contract_hours",
                      "expected_hours", "actual_hours", "difference", "shifts_count"):
            assert field in e, f"Missing field: {field}"

    def test_summary_group_filter(self, sync_client: TestClient):
        """Group filter returns only members of that group."""
        res = sync_client.get("/api/v1/groups")
        groups = res.json()
        if not groups:
            pytest.skip("No groups in DB")

        # Find a group with members
        target_group = None
        for g in groups:
            gid = g["ID"]
            member_res = sync_client.get(f"/api/v1/groups/{gid}/members")
            if member_res.status_code == 200 and member_res.json():
                target_group = g
                break

        if target_group is None:
            pytest.skip("No group with members found")

        gid = target_group["ID"]
        member_res = sync_client.get(f"/api/v1/groups/{gid}/members")
        member_ids = {m["ID"] for m in member_res.json()}

        r = sync_client.get(f"/api/v1/overtime/summary?year=2024&month=1&group_id={gid}")
        assert r.status_code == 200
        data = r.json()
        assert data["group_id"] == gid
        returned_ids = {e["employee_id"] for e in data["employees"]}
        # All returned employees must be in the group
        assert returned_ids.issubset(member_ids), (
            f"Overtime summary returned non-group employees: {returned_ids - member_ids}"
        )

    def test_summary_empty_group(self, sync_client: TestClient):
        """Group with no members returns empty employees list."""
        # Use a non-existent group_id to get empty result
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=1&group_id=999999")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["employees"] == []

    def test_summary_no_shifts_month(self, sync_client: TestClient):
        """Summary for a far-future month with no shifts: all actual_hours=0."""
        r = sync_client.get("/api/v1/overtime/summary?year=2099&month=1")
        assert r.status_code == 200
        data = r.json()
        for e in data["employees"]:
            assert e["actual_hours"] == 0.0
            assert e["shifts_count"] == 0

    def test_summary_leap_year_february(self, sync_client: TestClient):
        """Summary for Feb 2024 (leap year) returns correct working_days calculation."""
        r = sync_client.get("/api/v1/overtime/summary?year=2024&month=2")
        assert r.status_code == 200
        data = r.json()
        # Verify expected_hours uses 21 working days for Feb 2024
        for e in data["employees"]:
            if e["contract_hours"] > 0:
                expected = round(e["contract_hours"] * 21 / 5, 2)
                assert abs(e["expected_hours"] - expected) < 0.01, (
                    f"Expected hours mismatch for {e['employee_name']}: "
                    f"{e['expected_hours']} vs {expected}"
                )

    def test_planer_can_access_summary(self, app):
        """Planer can access the summary endpoint."""
        from api.main import _sessions

        client, tok = _planer_client(app)
        try:
            r = client.get("/api/v1/overtime/summary?year=2024&month=1")
            assert r.status_code == 200
        finally:
            _sessions.pop(tok, None)

    def test_reader_cannot_access_summary(self, app):
        """Leser (read-only) role should NOT be able to access the summary."""
        from api.main import _sessions

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 801,
            "NAME": "test_leser_overtime",
            "role": "Leser",
            "ADMIN": False,
            "RIGHTS": 1,
        }
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        try:
            r = client.get("/api/v1/overtime/summary?year=2024&month=1")
            assert r.status_code == 403
        finally:
            _sessions.pop(tok, None)

    def test_reader_cannot_access_employee_overtime(self, app):
        """Leser role should NOT be able to access single-employee overtime endpoint."""
        from api.main import _sessions

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 802,
            "NAME": "test_leser_emp_overtime",
            "role": "Leser",
            "ADMIN": False,
            "RIGHTS": 1,
        }
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        try:
            r = client.get("/api/v1/employees/1/overtime?year=2024&month=1")
            assert r.status_code == 403
        finally:
            _sessions.pop(tok, None)
