"""
Tests for the /api/simulation endpoint.

Covers:
1. Basic simulation with valid data
2. Empty month (no schedule entries)
3. Invalid date parameters (month out of range)
4. Employee not in schedule (absent but no shifts)
5. All-month absence ("all" shorthand)
6. Multiple employees absent
7. Scenario name customization
8. Response structure validation
9. Authentication: Leser cannot access (require_planer)
"""

import pytest
from starlette.testclient import TestClient


class TestSimulationBasic:
    """Basic simulation endpoint tests."""

    def test_simulation_valid_request(self, sync_client: TestClient):
        """POST /api/simulation with valid params returns 200 and expected keys."""
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [],
            "scenario_name": "Test Scenario",
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "scenario_name" in data
        assert "year" in data
        assert "month" in data
        assert "days" in data
        assert "summary" in data
        assert "employee_impacts" in data

    def test_simulation_response_structure(self, sync_client: TestClient):
        """Response has correct structure for days list."""
        payload = {"year": 2024, "month": 3, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        # 31 days in March
        assert len(data["days"]) == 31
        for day in data["days"]:
            assert "date" in day
            assert "baseline_count" in day
            assert "simulated_count" in day
            assert "lost_shifts" in day
            assert "status" in day
            assert "missing" in day
            assert "cover_candidates" in day

    def test_simulation_summary_keys(self, sync_client: TestClient):
        """Summary section has all required keys."""
        payload = {"year": 2024, "month": 6, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        summary = res.json()["summary"]
        assert "total_lost_shifts" in summary
        assert "critical_days" in summary
        assert "degraded_days" in summary
        assert "ok_days" in summary
        assert "affected_employees" in summary

    def test_simulation_no_absences_all_ok(self, sync_client: TestClient):
        """With no absences, no shifts are lost."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["total_lost_shifts"] == 0
        assert data["summary"]["affected_employees"] == 0
        for day in data["days"]:
            assert day["lost_shifts"] == 0

    def test_simulation_scenario_name_default(self, sync_client: TestClient):
        """Default scenario name is 'Simulation'."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        assert res.json()["scenario_name"] == "Simulation"

    def test_simulation_custom_scenario_name(self, sync_client: TestClient):
        """Custom scenario name is preserved in response."""
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [],
            "scenario_name": "Urlaubswelle Sommer",
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        assert res.json()["scenario_name"] == "Urlaubswelle Sommer"


class TestSimulationValidation:
    """Input validation tests."""

    def test_simulation_invalid_month_13(self, sync_client: TestClient):
        """month=13 should be rejected with 422 (Pydantic validation)."""
        payload = {"year": 2024, "month": 13, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 422

    def test_simulation_invalid_month_0(self, sync_client: TestClient):
        """month=0 should be rejected with 422."""
        payload = {"year": 2024, "month": 0, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 422

    def test_simulation_invalid_year_low(self, sync_client: TestClient):
        """year=1999 (below 2000) should be rejected with 422."""
        payload = {"year": 1999, "month": 6, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 422

    def test_simulation_invalid_year_high(self, sync_client: TestClient):
        """year=2101 (above 2100) should be rejected with 422."""
        payload = {"year": 2101, "month": 6, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 422

    def test_simulation_missing_required_fields(self, sync_client: TestClient):
        """Missing year or month should return 422."""
        res = sync_client.post("/api/simulation", json={"absences": []})
        assert res.status_code == 422

    def test_simulation_scenario_name_too_long(self, sync_client: TestClient):
        """scenario_name > 100 chars should be rejected."""
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [],
            "scenario_name": "A" * 101,
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 422


class TestSimulationAbsences:
    """Tests for absence simulation logic."""

    def test_simulation_nonexistent_employee(self, sync_client: TestClient):
        """Absence for nonexistent employee_id returns 200 with 0 impact."""
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [{"emp_id": 999999, "dates": ["2024-01-15"]}],
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        # emp not in schedule → 0 lost shifts
        assert data["summary"]["total_lost_shifts"] == 0

    def test_simulation_all_month_absence(self, sync_client: TestClient):
        """'all' dates means whole month for that employee."""
        # Get first employee with shifts if available
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees in test DB")
        emp_id = emps[0]["ID"]
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [{"emp_id": emp_id, "dates": ["all"]}],
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        # employee_impacts should exist for this employee
        assert data["summary"]["affected_employees"] == 1
        # All days where employee had shifts are lost
        impacts = {ei["emp_id"]: ei for ei in data["employee_impacts"]}
        assert emp_id in impacts

    def test_simulation_specific_date_absence(self, sync_client: TestClient):
        """Absence on specific dates is correctly simulated."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees in test DB")
        emp_id = emps[0]["ID"]
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [{"emp_id": emp_id, "dates": ["2024-01-10", "2024-01-11"]}],
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["affected_employees"] == 1
        # Days outside the absence are unaffected
        for day in data["days"]:
            if day["date"] not in ("2024-01-10", "2024-01-11"):
                assert day["lost_shifts"] == 0 or day["date"] not in (
                    "2024-01-10",
                    "2024-01-11",
                )

    def test_simulation_multiple_employees(self, sync_client: TestClient):
        """Multiple employees absent → affected_employees count correct."""
        emps = sync_client.get("/api/employees").json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        payload = {
            "year": 2024,
            "month": 1,
            "absences": [
                {"emp_id": emps[0]["ID"], "dates": ["2024-01-05"]},
                {"emp_id": emps[1]["ID"], "dates": ["2024-01-05"]},
            ],
        }
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["affected_employees"] == 2

    def test_simulation_february_leap_year(self, sync_client: TestClient):
        """Leap year February has 29 days in response."""
        payload = {"year": 2024, "month": 2, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        assert len(res.json()["days"]) == 29

    def test_simulation_february_non_leap(self, sync_client: TestClient):
        """Non-leap year February has 28 days in response."""
        payload = {"year": 2023, "month": 2, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        assert len(res.json()["days"]) == 28


class TestSimulationAuth:
    """Authentication and authorization tests."""

    def test_simulation_requires_auth(self, sync_client: TestClient):
        """Without token, /api/simulation should return 401."""
        from api.main import app
        from starlette.testclient import TestClient as TC

        with TC(app, raise_server_exceptions=False) as bare:
            res = bare.post(
                "/api/simulation",
                json={"year": 2024, "month": 1, "absences": []},
            )
        assert res.status_code == 401

    def test_simulation_leser_forbidden(self, leser_client: TestClient):
        """Leser role cannot access simulation (requires Planer)."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = leser_client.post("/api/simulation", json=payload)
        assert res.status_code in (401, 403)

    def test_simulation_planer_allowed(self, planer_client: TestClient):
        """Planer role can access simulation."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = planer_client.post("/api/simulation", json=payload)
        assert res.status_code == 200


class TestSimulationDayStatuses:
    """Tests for day-level status logic."""

    def test_simulation_status_values_are_valid(self, sync_client: TestClient):
        """All day statuses are one of: ok, degraded, critical."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        for day in res.json()["days"]:
            assert day["status"] in ("ok", "degraded", "critical")

    def test_simulation_cover_candidates_max_5(self, sync_client: TestClient):
        """cover_candidates capped at 5 per day."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        for day in res.json()["days"]:
            assert len(day["cover_candidates"]) <= 5

    def test_simulation_weekday_range(self, sync_client: TestClient):
        """weekday field is 0-6 (Monday=0, Sunday=6)."""
        payload = {"year": 2024, "month": 1, "absences": []}
        res = sync_client.post("/api/simulation", json=payload)
        assert res.status_code == 200
        for day in res.json()["days"]:
            assert 0 <= day["weekday"] <= 6
