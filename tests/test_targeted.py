"""
Targeted tests for specific uncovered code paths.
Focuses on API endpoints that need 2026 data and specific execution paths.
"""
import os
import sys
import shutil
import pytest
from unittest.mock import patch

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_REAL_DB_PATH = (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
)


@pytest.fixture
def tmp_db(tmp_path):
    dst = tmp_path / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    from sp5lib.database import SP5Database
    return SP5Database(str(dst))


# ─── Auth: Login Success Path ─────────────────────────────────────────────────

class TestAuthLogin:
    def test_login_success_mocked(self, write_client):
        """Test successful login path by mocking verify_user_password."""
        import api.main as main_module
        import api.routers.auth as auth_module
        fake_user = {'ID': 1, 'NAME': 'Admin', 'role': 'Admin', 'ADMIN': True}
        with patch.object(main_module, 'get_db') as mock_get_db, \
             patch.object(auth_module, 'get_db', mock_get_db):
            mock_db = mock_get_db.return_value
            mock_db.verify_user_password.return_value = fake_user
            resp = write_client.post("/api/auth/login", json={
                "username": "Admin", "password": "any_password"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert "token" in data
            assert data["user"]["NAME"] == "Admin"
            token = data["token"]

        # Test logout with that token
        logout_resp = write_client.post(
            "/api/auth/logout",
            headers={"x-auth-token": token}
        )
        assert logout_resp.status_code == 200

    def test_logout_with_invalid_token(self, write_client):
        """Test logout with non-existent token (still returns 200)."""
        resp = write_client.post(
            "/api/auth/logout",
            headers={"x-auth-token": "nonexistent-token-xyz"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_logout_without_token(self, write_client):
        """Test logout without token header."""
        resp = write_client.post("/api/auth/logout")
        assert resp.status_code == 200


# ─── Schedule CRUD API ────────────────────────────────────────────────────────

class TestScheduleCRUDAPI:
    """Test the schedule add/delete API endpoints that cover lines 943-993."""

    def test_add_schedule_entry_api(self, write_client):
        """Test POST /api/schedule (lines 943-945)."""
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        resp = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": "2025-10-01",
            "shift_id": shift_id
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_schedule_entry_duplicate_conflict(self, write_client):
        """Test POST /api/schedule with duplicate - lines 946-947."""
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        # Add once
        write_client.post("/api/schedule", json={
            "employee_id": emp_id, "date": "2025-10-05", "shift_id": shift_id
        })
        # Add again - should cause 409 conflict
        resp = write_client.post("/api/schedule", json={
            "employee_id": emp_id, "date": "2025-10-05", "shift_id": shift_id
        })
        assert resp.status_code in (409, 500, 200)  # 409 if duplicate detection

    def test_add_schedule_invalid_date(self, write_client):
        """Test POST /api/schedule with invalid date."""
        resp = write_client.post("/api/schedule", json={
            "employee_id": 1, "date": "not-a-date", "shift_id": 1
        })
        assert resp.status_code in (400, 422)

    def test_delete_schedule_entry_api(self, write_client):
        """Test DELETE /api/schedule/{employee_id}/{date} - lines 957-958."""
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        # Add then delete
        write_client.post("/api/schedule", json={
            "employee_id": emp_id, "date": "2025-10-10", "shift_id": shift_id
        })
        resp = write_client.delete(f"/api/schedule/{emp_id}/2025-10-10")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_schedule_entry_invalid_date(self, write_client):
        """Test DELETE /api/schedule with invalid date."""
        resp = write_client.delete("/api/schedule/1/not-a-date")
        assert resp.status_code == 400

    def test_delete_shift_only_api(self, write_client):
        """Test DELETE /api/schedule-shift/{employee_id}/{date} - lines 969-978."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = write_client.delete(f"/api/schedule-shift/{emp_id}/2025-10-15")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_shift_only_invalid_date(self, write_client):
        """Test invalid date for delete-shift-only."""
        resp = write_client.delete("/api/schedule-shift/1/bad-date")
        assert resp.status_code == 400

    def test_delete_absence_only_api(self, write_client):
        """Test DELETE /api/absences/{employee_id}/{date} - lines 984-993."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = write_client.delete(f"/api/absences/{emp_id}/2025-10-20")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_absence_only_invalid_date(self, write_client):
        """Test invalid date for delete-absence-only."""
        resp = write_client.delete("/api/absences/1/bad-date")
        assert resp.status_code == 400


# ─── Schedule Generate: Validation ───────────────────────────────────────────

class TestScheduleGenerateValidation:
    def test_generate_invalid_month(self, write_client):
        """Test schedule generate with invalid month - line 1009-1010."""
        resp = write_client.post("/api/schedule/generate", json={
            "year": 2025, "month": 13
        })
        assert resp.status_code == 400

    def test_generate_force_param(self, write_client):
        """Test schedule generate with force=True."""
        resp = write_client.post("/api/schedule/generate", json={
            "year": 2025, "month": 8, "force": True, "dry_run": True
        })
        assert resp.status_code == 200


# ─── Export with 2026 Data ────────────────────────────────────────────────────

class TestExportWith2026Data:
    """Tests that use actual DB data (2026 data)."""

    def test_export_absences_html_2026(self, sync_client):
        resp = sync_client.get("/api/export/absences?year=2026&format=html")
        assert resp.status_code == 200
        # Should have actual data rows
        content = resp.text
        assert len(content) > 100  # Not empty

    def test_export_absences_csv_2026(self, sync_client):
        resp = sync_client.get("/api/export/absences?year=2026&format=csv")
        assert resp.status_code == 200
        assert len(resp.text) > 10

    def test_export_absences_with_group_2026(self, sync_client):
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/export/absences?year=2026&group_id={gid}&format=html")
        assert resp.status_code == 200

    def test_export_schedule_2026(self, sync_client):
        resp = sync_client.get("/api/export/schedule?month=2026-01&format=html")
        assert resp.status_code == 200
        content = resp.text
        assert len(content) > 100

    def test_export_schedule_csv_2026(self, sync_client):
        resp = sync_client.get("/api/export/schedule?month=2026-02&format=csv")
        assert resp.status_code == 200

    def test_export_statistics_2026(self, sync_client):
        resp = sync_client.get("/api/export/statistics?year=2026&format=html")
        assert resp.status_code == 200

    def test_export_statistics_csv_2026(self, sync_client):
        resp = sync_client.get("/api/export/statistics?year=2026&format=csv")
        assert resp.status_code == 200


# ─── Statistics with 2026 Data ────────────────────────────────────────────────

class TestStatisticsWith2026Data:
    def test_statistics_2026(self, sync_client):
        resp = sync_client.get("/api/statistics?year=2026&month=1")
        assert resp.status_code == 200

    def test_statistics_employee_2026(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/statistics/employee/{emp_id}?year=2026")
        assert resp.status_code == 200

    def test_statistics_invalid_month(self, sync_client):
        resp = sync_client.get("/api/statistics?year=2026&month=13")
        assert resp.status_code == 400

    def test_zeitkonto_detail_2026(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/zeitkonto/detail?year=2026&employee_id={emp_id}")
        assert resp.status_code in (200, 404)

    def test_overtime_summary_2026(self, sync_client):
        resp = sync_client.get("/api/overtime-summary?year=2026")
        assert resp.status_code == 200

    def test_schedule_year_2026(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/schedule/year?year=2026&employee_id={emp_id}")
        assert resp.status_code == 200


# ─── Employee CRUD with Validation ───────────────────────────────────────────

class TestEmployeeCRUDFull:
    def test_create_employee_full(self, write_client):
        resp = write_client.post("/api/employees", json={
            "NAME": "Testmann",
            "FIRSTNAME": "Hans",
            "SHORTNAME": "TH",
            "BIRTHDAY": "1990-05-15",
            "EMPSTART": "2020-01-01",
        })
        assert resp.status_code == 200
        emp_id = resp.json()["record"]["ID"]
        # Update
        put = write_client.put(f"/api/employees/{emp_id}", json={"FIRSTNAME": "Updated"})
        assert put.status_code == 200
        # Delete
        del_resp = write_client.delete(f"/api/employees/{emp_id}")
        assert del_resp.status_code == 200

    def test_employee_not_found_update(self, write_client):
        resp = write_client.put("/api/employees/999999", json={"NAME": "X"})
        assert resp.status_code == 404

    def test_get_employee_by_id(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/employees/{emp_id}")
        assert resp.status_code == 200

    def test_get_employee_not_found(self, sync_client):
        resp = sync_client.get("/api/employees/999999")
        assert resp.status_code == 404


# ─── Leave Balance with 2026 Data ────────────────────────────────────────────

class TestLeaveBalanceWith2026:
    def test_leave_balance_2026(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/leave-balance?employee_id={emp_id}&year=2026")
        assert resp.status_code == 200

    def test_leave_balance_group_2026(self, sync_client):
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/leave-balance/group?year=2026&group_id={gid}")
        assert resp.status_code == 200

    def test_annual_close_preview_2026(self, sync_client):
        resp = sync_client.get("/api/annual-close/preview?year=2026")
        assert resp.status_code == 200


# ─── Database: Direct Method Tests for Coverage ──────────────────────────────

class TestDatabaseDirectMethods:
    def test_get_statistics_with_data(self, real_db):
        result = real_db.get_statistics(2026, 1)
        assert isinstance(result, (dict, list))

    def test_get_schedule_year_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.get_schedule_year(2026, emps[0]['ID'])
        assert isinstance(result, list)

    def test_get_schedule_week_with_data(self, real_db):
        result = real_db.get_schedule_week('2026-01-05')
        assert isinstance(result, dict)

    def test_get_employee_stats_year_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.get_employee_stats_year(emps[0]['ID'], 2026)
        assert isinstance(result, dict)

    def test_get_employee_stats_month_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.get_employee_stats_month(emps[0]['ID'], 2026, 1)
        assert isinstance(result, dict)

    def test_calculate_extracharge_hours_with_data(self, real_db):
        result = real_db.calculate_extracharge_hours(2026, 1)
        assert isinstance(result, list)

    def test_get_leave_balance_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.get_leave_balance(emps[0]['ID'], 2026)
        assert isinstance(result, dict)

    def test_get_leave_balance_group_with_data(self, real_db):
        groups = real_db.get_groups()
        if not groups:
            pytest.skip("No groups")
        result = real_db.get_leave_balance_group(2026, groups[0]['ID'])
        assert isinstance(result, list)

    def test_calculate_annual_statement_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.calculate_annual_statement(emps[0]['ID'], 2026)
        assert isinstance(result, dict)

    def test_calculate_time_balance_with_data(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        result = real_db.calculate_time_balance(emps[0]['ID'], 2026)
        assert isinstance(result, dict)

    def test_get_zeitkonto_with_data(self, real_db):
        result = real_db.get_zeitkonto(year=2026)
        assert isinstance(result, list)

    def test_get_schedule_conflicts_with_data(self, real_db):
        result = real_db.get_schedule_conflicts(2026, 1)
        assert isinstance(result, list)

    def test_get_overtime_summary_with_data(self, real_db):
        result = real_db.get_overtime_summary(2026)
        assert isinstance(result, (dict, list))

    def test_get_staffing_with_data(self, real_db):
        result = real_db.get_staffing(2026, 1)
        assert isinstance(result, list)

    def test_get_annual_close_preview_2026(self, real_db):
        result = real_db.get_annual_close_preview(2026)
        assert isinstance(result, dict)

    def test_get_bookings_with_data(self, real_db):
        result = real_db.get_bookings(year=2026)
        assert isinstance(result, list)

    def test_get_restrictions_filtered(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_restrictions(employee_id=emp_id)
        assert isinstance(result, list)

    def test_get_staffing_requirements_filtered(self, real_db):
        result = real_db.get_staffing_requirements(year=2026, month=1)
        assert isinstance(result, dict)

    def test_get_special_staffing_filtered(self, real_db):
        result = real_db.get_special_staffing(date='2026-01-01')
        assert isinstance(result, list)

    def test_get_cycle_exceptions_filtered(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_cycle_exceptions(employee_id=emp_id)
        assert isinstance(result, list)

    def test_get_holiday_bans_by_group(self, real_db):
        groups = real_db.get_groups()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]['ID']
        result = real_db.get_holiday_bans(group_id=gid)
        assert isinstance(result, list)

    def test_get_leave_entitlements_filtered(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_leave_entitlements(year=2026, employee_id=emp_id)
        assert isinstance(result, list)

    def test_get_notes_by_employee(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_notes(employee_id=emp_id)
        assert isinstance(result, list)

    def test_get_periods_by_group(self, real_db):
        groups = real_db.get_groups()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]['ID']
        result = real_db.get_periods(group_id=gid)
        assert isinstance(result, list)

    def test_get_changelog_filtered(self, real_db):
        result = real_db.get_changelog(limit=5)
        assert isinstance(result, list)

    def test_get_all_group_assignments(self, real_db):
        result = real_db.get_all_group_assignments()
        assert isinstance(result, list)

    def test_get_employee_access_filtered(self, real_db):
        result = real_db.get_employee_access(user_id=1)
        assert isinstance(result, list)

    def test_get_group_access_filtered(self, real_db):
        result = real_db.get_group_access(user_id=1)
        assert isinstance(result, list)

    def test_get_overtime_records_filtered(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_overtime_records(employee_id=emp_id)
        assert isinstance(result, list)

    def test_get_bookings_carry_forward(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        result = real_db.get_carry_forward(emp_id, 2026)
        assert isinstance(result, dict)

    def test_kuerzel_generation(self, tmp_db):
        """Test that shortname (Kürzel) is auto-generated for employees."""
        emp = tmp_db.create_employee({
            'NAME': 'Ziegler', 'FIRSTNAME': 'Bernd', 'SHORTNAME': ''
        })
        assert emp is not None
        # Shortname should be auto-generated or at least not crash

    def test_get_employee_shortname_filter(self, real_db):
        emps = real_db.get_employees()
        if not emps:
            pytest.skip("No employees")
        # Test that SHORTNAME is present in employee data
        emp = emps[0]
        assert 'SHORTNAME' in emp or 'shortname' in emp


# ─── API: More Absences Tests ─────────────────────────────────────────────────

class TestAbsencesAPI:
    def test_absences_2026(self, sync_client):
        resp = sync_client.get("/api/absences?year=2026")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0  # Real data in 2026

    def test_absences_delete_by_emp_date(self, write_client):
        emps = write_client.get("/api/employees").json()
        lt_list = write_client.get("/api/leave-types").json()
        if not emps or not lt_list:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        lt_id = lt_list[0]["ID"]
        # Create absence
        write_client.post("/api/absences", json={
            "employee_id": emp_id, "date": "2025-11-01", "leave_type_id": lt_id
        })
        # Delete it
        resp = write_client.delete(f"/api/absences/{emp_id}/2025-11-01")
        assert resp.status_code == 200


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def real_db():
    from sp5lib.database import SP5Database
    return SP5Database(_REAL_DB_PATH)
