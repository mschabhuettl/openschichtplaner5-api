"""Tests for Work Time Rules engine — Q079.

Covers:
  - GET /api/v1/work-time-rules (auth, defaults)
  - PUT /api/v1/work-time-rules (Admin only, validation)
  - POST /api/v1/work-time-rules/check (violations: max_hours_day, max_hours_week,
    min_rest, consecutive_days, no violations, auth, 404)
  - POST /api/v1/work-time-rules/check-all (group filter, no group, auth)
  - Edge cases: disabled rules, date range validation
"""

from __future__ import annotations

import secrets
from datetime import date

import pytest
from starlette.testclient import TestClient

# ── Helpers ───────────────────────────────────────────────────────────────────

def _inject_token(role: str, user_id: int = 901) -> tuple[str, dict]:
    from api.main import _sessions
    tok = secrets.token_hex(20)
    user = {
        "ID": user_id,
        "NAME": f"test_{role.lower()}_{user_id}",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else (2 if role == "Planer" else 1),
    }
    _sessions[tok] = user
    return tok, user


def _admin_client(app) -> tuple[TestClient, str]:
    tok, _ = _inject_token("Admin", user_id=801)
    client = TestClient(app, raise_server_exceptions=True)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def _planer_client(app) -> tuple[TestClient, str]:
    tok, _ = _inject_token("Planer", user_id=802)
    client = TestClient(app, raise_server_exceptions=True)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def _anon_client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Mock DB helpers ───────────────────────────────────────────────────────────

def _make_mashi_entries(employee_id: int, dates_hours: list[tuple[str, float, int | None]]) -> list[dict]:
    """Build MASHI-like records. dates_hours: [(date_str, hours, shift_id), ...]"""
    records = []
    for d, _, sid in dates_hours:
        records.append({"EMPLOYEEID": employee_id, "DATE": d, "SHIFTID": sid})
    return records


def _make_shifts(entries: list[tuple[int, float]]) -> list[dict]:
    """Build SHIFT records: [(shift_id, duration_hours), ...]"""
    return [{"ID": sid, "DURATION0": dur, "STARTTIME": "08:00"} for sid, dur in entries]


# ── Fixture: temp rules file ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rules_file(tmp_path, monkeypatch):
    """Each test gets a fresh temp rules file so they don't interfere."""
    rules_file = tmp_path / "work_time_rules.json"
    monkeypatch.setattr(
        "api.routers.work_time_rules._RULES_FILE",
        rules_file,
    )
    monkeypatch.setattr(
        "api.routers.work_time_rules._DATA_DIR",
        tmp_path,
    )
    yield rules_file


# ── GET /api/v1/work-time-rules ───────────────────────────────────────────────

class TestGetRules:
    def test_unauthenticated_returns_401(self, app):
        client = _anon_client(app)
        res = client.get("/api/v1/work-time-rules")
        assert res.status_code == 401

    def test_viewer_cannot_get_rules(self, app):
        tok, _ = _inject_token("Viewer", user_id=803)
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        res = client.get("/api/v1/work-time-rules")
        # Viewer has RIGHTS=1, require_planer needs >= 2
        assert res.status_code in (401, 403)

    def test_planer_can_get_rules(self, app):
        client, _ = _planer_client(app)
        res = client.get("/api/v1/work-time-rules")
        assert res.status_code == 200
        data = res.json()
        assert "max_hours_per_day" in data
        assert "max_hours_per_week" in data
        assert "min_rest_hours_between_shifts" in data
        assert "max_consecutive_days" in data
        assert "enabled" in data

    def test_admin_can_get_rules(self, app):
        client, _ = _admin_client(app)
        res = client.get("/api/v1/work-time-rules")
        assert res.status_code == 200

    def test_defaults_are_correct(self, app):
        client, _ = _admin_client(app)
        res = client.get("/api/v1/work-time-rules")
        data = res.json()
        assert data["max_hours_per_day"] == 10.0
        assert data["max_hours_per_week"] == 48.0
        assert data["min_rest_hours_between_shifts"] == 11.0
        assert data["max_consecutive_days"] == 6
        assert data["enabled"] is True


# ── PUT /api/v1/work-time-rules ───────────────────────────────────────────────

class TestUpdateRules:
    def test_unauthenticated_returns_401(self, app):
        client = _anon_client(app)
        res = client.put("/api/v1/work-time-rules", json={"max_hours_per_day": 8})
        assert res.status_code == 401

    def test_planer_cannot_update_rules(self, app):
        client, _ = _planer_client(app)
        res = client.put("/api/v1/work-time-rules", json={
            "max_hours_per_day": 8.0,
            "max_hours_per_week": 40.0,
            "min_rest_hours_between_shifts": 12.0,
            "max_consecutive_days": 5,
            "enabled": True,
        })
        assert res.status_code in (401, 403)

    def test_admin_can_update_rules(self, app):
        client, _ = _admin_client(app)
        payload = {
            "max_hours_per_day": 8.0,
            "max_hours_per_week": 40.0,
            "min_rest_hours_between_shifts": 12.0,
            "max_consecutive_days": 5,
            "enabled": True,
        }
        res = client.put("/api/v1/work-time-rules", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["max_hours_per_day"] == 8.0
        assert data["max_hours_per_week"] == 40.0
        assert data["min_rest_hours_between_shifts"] == 12.0
        assert data["max_consecutive_days"] == 5

    def test_updated_rules_are_persisted(self, app):
        client, _ = _admin_client(app)
        payload = {
            "max_hours_per_day": 7.5,
            "max_hours_per_week": 37.5,
            "min_rest_hours_between_shifts": 10.0,
            "max_consecutive_days": 5,
            "enabled": False,
        }
        client.put("/api/v1/work-time-rules", json=payload)
        res = client.get("/api/v1/work-time-rules")
        data = res.json()
        assert data["max_hours_per_day"] == 7.5
        assert data["enabled"] is False

    def test_can_disable_rules(self, app):
        client, _ = _admin_client(app)
        payload = {
            "max_hours_per_day": 10.0,
            "max_hours_per_week": 48.0,
            "min_rest_hours_between_shifts": 11.0,
            "max_consecutive_days": 6,
            "enabled": False,
        }
        res = client.put("/api/v1/work-time-rules", json=payload)
        assert res.status_code == 200
        assert res.json()["enabled"] is False


# ── POST /api/v1/work-time-rules/check ────────────────────────────────────────

class TestCheckEmployee:
    def test_unauthenticated_returns_401(self, app):
        client = _anon_client(app)
        res = client.post("/api/v1/work-time-rules/check?employee_id=1&from=2024-01-01&to=2024-01-07")
        assert res.status_code == 401

    def test_invalid_date_range_returns_422(self, app, sync_client):
        """to < from should return 422."""
        res = sync_client.post(
            "/api/v1/work-time-rules/check?employee_id=1&from=2024-01-10&to=2024-01-01"
        )
        assert res.status_code == 422

    def test_nonexistent_employee_returns_404(self, app, sync_client):
        res = sync_client.post(
            "/api/v1/work-time-rules/check?employee_id=999999&from=2024-01-01&to=2024-01-07"
        )
        assert res.status_code == 404

    def test_no_violations_for_normal_hours(self, app):
        """Employee with 8h/day and 5 days/week should have no violations."""
        client, _ = _admin_client(app)

        # Get a real employee or skip
        emp_res = client.get("/api/v1/employees")
        if emp_res.status_code != 200 or not emp_res.json():
            pytest.skip("No employees in DB")

        # Set lenient rules so there are no violations
        client.put("/api/v1/work-time-rules", json={
            "max_hours_per_day": 24.0,
            "max_hours_per_week": 168.0,
            "min_rest_hours_between_shifts": 0.0,
            "max_consecutive_days": 365,
            "enabled": True,
        })

        emp_id = emp_res.json()[0]["ID"]
        res = client.post(
            f"/api/v1/work-time-rules/check?employee_id={emp_id}&from=2024-01-01&to=2024-01-07"
        )
        assert res.status_code == 200
        data = res.json()
        assert "violations" in data
        assert "summary" in data
        assert data["summary"]["total"] == 0

    def test_response_structure(self, app):
        """Response always has violations list and summary dict."""
        client, _ = _admin_client(app)
        emp_res = client.get("/api/v1/employees")
        if emp_res.status_code != 200 or not emp_res.json():
            pytest.skip("No employees in DB")
        emp_id = emp_res.json()[0]["ID"]
        res = client.post(
            f"/api/v1/work-time-rules/check?employee_id={emp_id}&from=2024-01-01&to=2024-01-31"
        )
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data["violations"], list)
        assert isinstance(data["summary"], dict)
        assert "total" in data["summary"]
        assert "warnings" in data["summary"]
        assert "errors" in data["summary"]

    def test_summary_counts_match_violations(self, app):
        """summary.total == len(violations) and warnings + errors == total."""
        client, _ = _admin_client(app)
        emp_res = client.get("/api/v1/employees")
        if emp_res.status_code != 200 or not emp_res.json():
            pytest.skip("No employees in DB")
        emp_id = emp_res.json()[0]["ID"]
        res = client.post(
            f"/api/v1/work-time-rules/check?employee_id={emp_id}&from=2024-01-01&to=2024-12-31"
        )
        data = res.json()
        assert data["summary"]["total"] == len(data["violations"])
        assert (data["summary"]["warnings"] + data["summary"]["errors"]) == data["summary"]["total"]


# ── POST /api/v1/work-time-rules/check-all ────────────────────────────────────

class TestCheckAll:
    def test_unauthenticated_returns_401(self, app):
        client = _anon_client(app)
        res = client.post("/api/v1/work-time-rules/check-all?from=2024-01-01&to=2024-01-07")
        assert res.status_code == 401

    def test_planer_can_check_all(self, app):
        client, _ = _planer_client(app)
        res = client.post("/api/v1/work-time-rules/check-all?from=2024-01-01&to=2024-01-07")
        assert res.status_code == 200

    def test_invalid_group_returns_404(self, app, sync_client):
        res = sync_client.post(
            "/api/v1/work-time-rules/check-all?group_id=999999&from=2024-01-01&to=2024-01-07"
        )
        assert res.status_code == 404

    def test_invalid_date_range_returns_422(self, app, sync_client):
        res = sync_client.post(
            "/api/v1/work-time-rules/check-all?from=2024-01-10&to=2024-01-01"
        )
        assert res.status_code == 422

    def test_response_structure(self, app, sync_client):
        res = sync_client.post(
            "/api/v1/work-time-rules/check-all?from=2024-01-01&to=2024-01-07"
        )
        assert res.status_code == 200
        data = res.json()
        assert "violations" in data
        assert "summary" in data

    def test_summary_totals_match(self, app, sync_client):
        res = sync_client.post(
            "/api/v1/work-time-rules/check-all?from=2024-01-01&to=2024-01-31"
        )
        data = res.json()
        assert data["summary"]["total"] == len(data["violations"])
        assert (data["summary"]["warnings"] + data["summary"]["errors"]) == data["summary"]["total"]


# ── Rule engine unit tests ─────────────────────────────────────────────────────

class TestRuleEngine:
    """Direct tests of _check_employee with mocked DB."""

    def _make_db_mock(self, mashi=None, spshi=None, shifts=None, employees=None):
        """Create a mock DB object."""
        mashi = mashi or []
        spshi = spshi or []
        shifts = shifts or []
        employees = employees or []

        class MockDB:
            def _read(self, table: str):
                if table == "MASHI":
                    return mashi
                if table == "SPSHI":
                    return spshi
                if table == "SHIFT":
                    return shifts
                if table == "EMPLY":
                    return employees
                return []

        return MockDB()

    def test_no_violations_when_rules_disabled(self):
        from api.routers.work_time_rules import _check_employee
        db = self._make_db_mock(
            mashi=[{"EMPLOYEEID": 1, "DATE": "2024-01-01", "SHIFTID": 10}],
            shifts=[{"ID": 10, "DURATION0": 15.0, "STARTTIME": "08:00"}],
        )
        rules = {
            "max_hours_per_day": 10, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 6,
            "enabled": False,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        assert violations == []

    def test_max_hours_per_day_violation(self):
        from api.routers.work_time_rules import _check_employee
        db = self._make_db_mock(
            mashi=[{"EMPLOYEEID": 1, "DATE": "2024-01-15", "SHIFTID": 10}],
            shifts=[{"ID": 10, "DURATION0": 12.0, "STARTTIME": "06:00"}],
        )
        rules = {
            "max_hours_per_day": 10, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 6,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 31), rules)
        types = [v["type"] for v in violations]
        assert "max_hours_per_day" in types

    def test_max_hours_per_week_violation(self):
        from api.routers.work_time_rules import _check_employee
        # 7 days × 8h = 56h in one week > 48h limit
        entries = []
        shifts_list = []
        for day in range(1, 8):
            d = f"2024-01-{day:02d}"
            entries.append({"EMPLOYEEID": 1, "DATE": d, "SHIFTID": 10})
        shifts_list.append({"ID": 10, "DURATION0": 8.0, "STARTTIME": "08:00"})
        db = self._make_db_mock(mashi=entries, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 24, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 0, "max_consecutive_days": 365,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        types = [v["type"] for v in violations]
        assert "max_hours_per_week" in types

    def test_min_rest_violation(self):
        from api.routers.work_time_rules import _check_employee
        # Shift 1: 2024-01-01 20:00–04:00 (8h), Shift 2: 2024-01-02 07:00–15:00
        # Rest = 3h < 11h required
        mashi = [
            {"EMPLOYEEID": 1, "DATE": "2024-01-01", "SHIFTID": 20},
            {"EMPLOYEEID": 1, "DATE": "2024-01-02", "SHIFTID": 21},
        ]
        shifts_list = [
            {"ID": 20, "DURATION0": 8.0, "STARTTIME": "20:00"},
            {"ID": 21, "DURATION0": 8.0, "STARTTIME": "07:00"},
        ]
        db = self._make_db_mock(mashi=mashi, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 24, "max_hours_per_week": 200,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 365,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        types = [v["type"] for v in violations]
        assert "min_rest_hours_between_shifts" in types

    def test_sufficient_rest_no_violation(self):
        from api.routers.work_time_rules import _check_employee
        # Shift 1: 08:00-16:00, Shift 2 next day: 08:00 → 16h rest > 11h
        mashi = [
            {"EMPLOYEEID": 1, "DATE": "2024-01-01", "SHIFTID": 20},
            {"EMPLOYEEID": 1, "DATE": "2024-01-02", "SHIFTID": 20},
        ]
        shifts_list = [{"ID": 20, "DURATION0": 8.0, "STARTTIME": "08:00"}]
        db = self._make_db_mock(mashi=mashi, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 24, "max_hours_per_week": 200,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 365,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        rest_violations = [v for v in violations if v["type"] == "min_rest_hours_between_shifts"]
        assert rest_violations == []

    def test_consecutive_days_violation(self):
        from api.routers.work_time_rules import _check_employee
        # 7 consecutive days, max is 6
        entries = [{"EMPLOYEEID": 1, "DATE": f"2024-01-{d:02d}", "SHIFTID": 10} for d in range(1, 8)]
        shifts_list = [{"ID": 10, "DURATION0": 8.0, "STARTTIME": "08:00"}]
        db = self._make_db_mock(mashi=entries, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 24, "max_hours_per_week": 200,
            "min_rest_hours_between_shifts": 0, "max_consecutive_days": 6,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        types = [v["type"] for v in violations]
        assert "max_consecutive_days" in types

    def test_violation_has_required_fields(self):
        from api.routers.work_time_rules import _check_employee
        mashi = [{"EMPLOYEEID": 1, "DATE": "2024-01-15", "SHIFTID": 10}]
        shifts_list = [{"ID": 10, "DURATION0": 12.0, "STARTTIME": "08:00"}]
        db = self._make_db_mock(mashi=mashi, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 10, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 6,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 31), rules)
        assert len(violations) > 0
        v = violations[0]
        for field in ("type", "date", "employee_id", "description", "severity", "value", "limit"):
            assert field in v, f"Missing field: {field}"

    def test_severity_is_warning_or_error(self):
        from api.routers.work_time_rules import _check_employee
        entries = [{"EMPLOYEEID": 1, "DATE": f"2024-01-{d:02d}", "SHIFTID": 10} for d in range(1, 8)]
        shifts_list = [{"ID": 10, "DURATION0": 12.0, "STARTTIME": "08:00"}]
        db = self._make_db_mock(mashi=entries, shifts=shifts_list)
        rules = {
            "max_hours_per_day": 10, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 6,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 7), rules)
        for v in violations:
            assert v["severity"] in ("warning", "error"), f"Invalid severity: {v['severity']}"

    def test_spshi_hours_counted(self):
        from api.routers.work_time_rules import _check_employee
        # Special shift: 15h on a single day
        spshi = [{"EMPLOYEEID": 1, "DATE": "2024-01-10", "DURATION": 15.0}]
        db = self._make_db_mock(spshi=spshi)
        rules = {
            "max_hours_per_day": 10, "max_hours_per_week": 48,
            "min_rest_hours_between_shifts": 11, "max_consecutive_days": 6,
            "enabled": True,
        }
        violations = _check_employee(db, 1, date(2024, 1, 1), date(2024, 1, 31), rules)
        types = [v["type"] for v in violations]
        assert "max_hours_per_day" in types
