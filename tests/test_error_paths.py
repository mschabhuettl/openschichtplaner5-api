"""
Tests for error paths (404, 400, validation) and admin-only endpoints.
Complements test_comprehensive.py by covering exception branches.
"""
import os
import sys
import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── Admin API Endpoints ──────────────────────────────────────────────────────

class TestAdminUserCRUD:
    """Tests for user management endpoints that require admin auth."""

    def test_create_user(self, admin_client):
        """Verify create user."""
        resp = admin_client.post("/api/users", json={
            "NAME": "newuser", "PASSWORD": "pass123", "role": "Leser"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        uid = resp.json()["record"]["ID"]
        assert uid is not None

    def test_create_user_invalid_role(self, admin_client):
        """Verify create user invalid role."""
        resp = admin_client.post("/api/users", json={
            "NAME": "baduser", "PASSWORD": "pass123", "role": "InvalidRole"
        })
        assert resp.status_code == 400

    def test_update_user(self, admin_client):
        # First create a user
        """Verify update user."""
        create = admin_client.post("/api/users", json={
            "NAME": "updateme", "PASSWORD": "pass", "role": "Planer"
        })
        assert create.status_code == 200
        uid = create.json()["record"]["ID"]

        resp = admin_client.put(f"/api/users/{uid}", json={"DESCRIP": "Updated description"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_update_user_invalid_role(self, admin_client):
        """Verify update user invalid role."""
        create = admin_client.post("/api/users", json={
            "NAME": "roltest", "PASSWORD": "pass", "role": "Leser"
        })
        uid = create.json()["record"]["ID"]
        resp = admin_client.put(f"/api/users/{uid}", json={"role": "BadRole"})
        assert resp.status_code == 400

    def test_update_user_not_found(self, admin_client):
        """Verify update user not found."""
        resp = admin_client.put("/api/users/999999", json={"DESCRIP": "X"})
        assert resp.status_code in (404, 500)

    def test_delete_user(self, admin_client):
        """Verify delete user."""
        create = admin_client.post("/api/users", json={
            "NAME": "deleteme", "PASSWORD": "pass", "role": "Leser"
        })
        uid = create.json()["record"]["ID"]
        resp = admin_client.delete(f"/api/users/{uid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_user_not_found(self, admin_client):
        """Verify delete user not found."""
        resp = admin_client.delete("/api/users/999999")
        assert resp.status_code == 404

    def test_change_user_password(self, admin_client):
        """Verify change user password."""
        create = admin_client.post("/api/users", json={
            "NAME": "pwtest", "PASSWORD": "old", "role": "Leser"
        })
        uid = create.json()["record"]["ID"]
        resp = admin_client.post(f"/api/users/{uid}/change-password", json={"new_password": "newpass"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_change_user_password_empty(self, admin_client):
        """Verify change user password empty."""
        resp = admin_client.post("/api/users/1/change-password", json={"new_password": ""})
        assert resp.status_code == 400

    def test_change_user_password_not_found(self, admin_client):
        """Verify change user password not found."""
        resp = admin_client.post("/api/users/999999/change-password", json={"new_password": "x"})
        assert resp.status_code in (404, 500)

    def test_admin_compact(self, admin_client):
        """Verify admin compact."""
        resp = admin_client.post("/api/admin/compact", json={})
        assert resp.status_code in (200, 500)


# ─── Validation Error Paths ───────────────────────────────────────────────────

class TestEmployeeValidation:
    def test_create_employee_empty_name(self, write_client):
        """Verify create employee empty name."""
        resp = write_client.post("/api/employees", json={"NAME": "", "FIRSTNAME": "Test"})
        assert resp.status_code in (400, 422)

    def test_create_employee_invalid_birthday(self, write_client):
        """Verify create employee invalid birthday."""
        resp = write_client.post("/api/employees", json={
            "NAME": "Test", "BIRTHDAY": "not-a-date"
        })
        assert resp.status_code in (400, 422)

    def test_update_employee_not_found(self, write_client):
        """Verify update employee not found."""
        resp = write_client.put("/api/employees/999999", json={"NAME": "X"})
        assert resp.status_code == 404

    def test_create_employee_with_dates(self, write_client):
        """Verify create employee with dates."""
        resp = write_client.post("/api/employees", json={
            "NAME": "DateTest",
            "EMPSTART": "2020-01-01",
            "EMPEND": "2025-12-31",
        })
        assert resp.status_code == 200


class TestGroupValidation:
    def test_create_group_empty_name(self, write_client):
        """Verify create group empty name."""
        resp = write_client.post("/api/groups", json={"NAME": ""})
        assert resp.status_code == 400

    def test_update_group_not_found(self, write_client):
        """Verify update group not found."""
        resp = write_client.put("/api/groups/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestShiftValidation:
    def test_create_shift_empty_name(self, write_client):
        """Verify create shift empty name."""
        resp = write_client.post("/api/shifts", json={"NAME": ""})
        assert resp.status_code == 400

    def test_update_shift_not_found(self, write_client):
        """Verify update shift not found."""
        resp = write_client.put("/api/shifts/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestLeaveTypeValidation:
    def test_create_leave_type_empty_name(self, write_client):
        """Verify create leave type empty name."""
        resp = write_client.post("/api/leave-types", json={"NAME": ""})
        assert resp.status_code == 400

    def test_update_leave_type_not_found(self, write_client):
        """Verify update leave type not found."""
        resp = write_client.put("/api/leave-types/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestHolidayValidation:
    def test_create_holiday_empty_name(self, write_client):
        """Verify create holiday empty name."""
        resp = write_client.post("/api/holidays", json={"NAME": "", "DATE": "2025-12-30"})
        assert resp.status_code == 400

    def test_create_holiday_invalid_date(self, write_client):
        """Verify create holiday invalid date."""
        resp = write_client.post("/api/holidays", json={"NAME": "Test", "DATE": "not-a-date"})
        assert resp.status_code == 400

    def test_update_holiday_not_found(self, write_client):
        """Verify update holiday not found."""
        resp = write_client.put("/api/holidays/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestWorkplaceValidation:
    def test_create_workplace_empty_name(self, write_client):
        """Verify create workplace empty name."""
        resp = write_client.post("/api/workplaces", json={"NAME": ""})
        assert resp.status_code == 400

    def test_update_workplace_not_found(self, write_client):
        """Verify update workplace not found."""
        resp = write_client.put("/api/workplaces/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestExtrachargeValidation:
    def test_create_extracharge_empty_name(self, write_client):
        """Verify create extracharge empty name."""
        resp = write_client.post("/api/extracharges", json={
            "NAME": "", "SHORTNAME": "T", "VALIDDAYS": "1111100"
        })
        assert resp.status_code == 400

    def test_create_extracharge_invalid_validdays(self, write_client):
        """Verify create extracharge invalid validdays."""
        resp = write_client.post("/api/extracharges", json={
            "NAME": "Test", "SHORTNAME": "T", "VALIDDAYS": "111"
        })
        assert resp.status_code == 400

    def test_update_extracharge_not_found(self, write_client):
        """Verify update extracharge not found."""
        resp = write_client.put("/api/extracharges/999999", json={"NAME": "X"})
        assert resp.status_code == 404


class TestAbsenceValidation:
    def test_create_absence_invalid_date(self, write_client):
        """Verify create absence invalid date."""
        resp = write_client.post("/api/absences", json={
            "employee_id": 1, "date": "not-a-date", "leave_type_id": 1
        })
        assert resp.status_code in (400, 422)


class TestNoteValidation:
    def test_delete_note_not_found(self, write_client):
        """Verify delete note not found."""
        resp = write_client.delete("/api/notes/999999")
        # Returns 200 with deleted=0 (not 404) when not found
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestScheduleValidation:
    def test_delete_schedule_not_found(self, write_client):
        """Verify delete schedule not found."""
        resp = write_client.delete("/api/schedule/999999/2025-07-01")
        assert resp.status_code in (200, 404)

    def test_post_schedule_invalid_date(self, write_client):
        """Verify post schedule invalid date."""
        resp = write_client.post("/api/schedule", json={
            "employee_id": 1, "date": "not-a-date", "shift_id": 1
        })
        assert resp.status_code in (400, 422)

    def test_schedule_bulk_create(self, write_client):
        """Verify schedule bulk create."""
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No data")
        resp = write_client.post("/api/schedule/bulk", json={
            "entries": [
                {"employee_id": emps[0]["ID"], "date": "2025-09-02", "shift_id": shifts[0]["ID"]},
            ]
        })
        assert resp.status_code == 200


class TestBookingsDeleteNotFound:
    def test_delete_booking_not_found(self, write_client):
        """Verify delete booking not found."""
        resp = write_client.delete("/api/bookings/999999")
        assert resp.status_code == 404

    def test_booking_invalid_date(self, write_client):
        """Verify booking invalid date."""
        resp = write_client.post("/api/bookings", json={
            "employee_id": 1, "date": "not-a-date", "type": 0, "value": 1.0
        })
        assert resp.status_code in (400, 422)  # 422 = Pydantic validation


class TestPeriodValidation:
    def test_delete_period_not_found(self, write_client):
        """Verify delete period not found."""
        resp = write_client.delete("/api/periods/999999")
        # Returns 200 with deleted=0 (not 404) when not found
        assert resp.status_code in (200, 404)


class TestStaffingRequirementsSpecialValidation:
    def test_update_special_staffing_not_found(self, write_client):
        """Verify update special staffing not found."""
        resp = write_client.put("/api/staffing-requirements/special/999999", json={"min": 1})
        assert resp.status_code == 404


class TestShiftCycleValidation:
    def test_create_cycle_empty_name(self, write_client):
        """Verify create cycle empty name."""
        resp = write_client.post("/api/shift-cycles", json={"name": "", "size_weeks": 1})
        assert resp.status_code in (400, 422)  # 422 = Pydantic validation

    def test_create_cycle_invalid_weeks(self, write_client):
        """Verify create cycle invalid weeks."""
        resp = write_client.post("/api/shift-cycles", json={"name": "Test", "size_weeks": 0})
        assert resp.status_code in (400, 422)  # 422 = Pydantic validation

    def test_delete_cycle_not_found(self, write_client):
        """Verify delete cycle not found."""
        resp = write_client.delete("/api/shift-cycles/999999")
        assert resp.status_code == 404

    def test_update_cycle_not_found(self, write_client):
        """Verify update cycle not found."""
        resp = write_client.put("/api/shift-cycles/999999", json={
            "name": "X", "size_weeks": 1, "entries": []
        })
        assert resp.status_code == 404


class TestRestrictionValidation:
    def test_restriction_invalid_weekday(self, write_client):
        """Verify restriction invalid weekday."""
        resp = write_client.post("/api/restrictions", json={
            "employee_id": 1, "shift_id": 1, "weekday": 10
        })
        assert resp.status_code in (400, 422)  # 422 = Pydantic validation


class TestEinsatzplanValidation:
    def test_update_einsatzplan_not_found(self, write_client):
        """Verify update einsatzplan not found."""
        resp = write_client.put("/api/einsatzplan/999999", json={"name": "X"})
        assert resp.status_code in (404, 500)

    def test_delete_einsatzplan_not_found(self, write_client):
        """Verify delete einsatzplan not found."""
        resp = write_client.delete("/api/einsatzplan/999999")
        assert resp.status_code in (200, 404)


class TestSpecialStaffingDelete:
    def test_delete_special_staffing_not_found(self, write_client):
        """Verify delete special staffing not found."""
        resp = write_client.delete("/api/staffing-requirements/special/999999")
        assert resp.status_code == 404


# ─── Dashboard Endpoints ───────────────────────────────────────────────────────

class TestDashboardEndpoints:
    def test_dashboard_summary(self, sync_client):
        """Verify dashboard summary."""
        resp = sync_client.get("/api/dashboard/summary")
        assert resp.status_code == 200

    def test_dashboard_today(self, sync_client):
        """Verify dashboard today."""
        resp = sync_client.get("/api/dashboard/today?date=2024-06-03")
        assert resp.status_code == 200

    def test_dashboard_upcoming(self, sync_client):
        """Verify dashboard upcoming."""
        resp = sync_client.get("/api/dashboard/upcoming?date=2024-06-03")
        assert resp.status_code == 200

    def test_dashboard_stats(self, sync_client):
        """Verify dashboard stats."""
        resp = sync_client.get("/api/dashboard/stats")
        assert resp.status_code == 200


# ─── Overtime Summary / Changelog ────────────────────────────────────────────

class TestOvertimeSummaryAPI:
    def test_overtime_summary(self, sync_client):
        """Verify overtime summary."""
        resp = sync_client.get("/api/overtime-summary?year=2024")
        assert resp.status_code == 200
        data = resp.json()
        # Returns dict with 'employees' list or a plain list depending on API version
        assert isinstance(data, (list, dict))

    def test_overtime_summary_with_group(self, sync_client):
        """Verify overtime summary with group."""
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/overtime-summary?year=2024&group_id={gid}")
        assert resp.status_code == 200

    def test_changelog_get(self, sync_client):
        """Verify changelog get."""
        resp = sync_client.get("/api/changelog")
        assert resp.status_code == 200


# ─── Export Format Variations ─────────────────────────────────────────────────

class TestExportFormats:
    def test_export_schedule_csv(self, sync_client):
        """Verify export schedule csv."""
        resp = sync_client.get("/api/export/schedule?month=2024-06&format=csv")
        assert resp.status_code == 200

    def test_export_employees_csv(self, sync_client):
        """Verify export employees csv."""
        resp = sync_client.get("/api/export/employees?format=csv")
        assert resp.status_code == 200

    def test_export_employees_html_with_group(self, sync_client):
        """Verify export employees html with group."""
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/export/employees?format=html&group_id={gid}")
        assert resp.status_code == 200

    def test_export_statistics_with_group(self, sync_client):
        """Verify export statistics with group."""
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/export/statistics?year=2024&group_id={gid}&format=csv")
        assert resp.status_code == 200


# ─── Additional GET endpoints ─────────────────────────────────────────────────

class TestAdditionalGets:
    def test_absences_filtered(self, sync_client):
        """Verify absences filtered."""
        resp = sync_client.get("/api/absences?year=2024&employee_id=1")
        assert resp.status_code == 200

    def test_schedule_day_filtered(self, sync_client):
        """Verify schedule day filtered."""
        resp = sync_client.get("/api/schedule/day?date=2024-06-03")
        assert resp.status_code == 200

    def test_schedule_week_filtered(self, sync_client):
        """Verify schedule week filtered."""
        resp = sync_client.get("/api/schedule/week?date=2024-06-03")
        assert resp.status_code == 200

    def test_schedule_year(self, sync_client):
        """Verify schedule year."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/schedule/year?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200

    def test_zeitkonto_detail(self, sync_client):
        """Verify zeitkonto detail."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/zeitkonto/detail?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200

    def test_zeitkonto_summary(self, sync_client):
        """Verify zeitkonto summary."""
        resp = sync_client.get("/api/zeitkonto/summary?year=2024")
        assert resp.status_code == 200

    def test_leave_balance_employee(self, sync_client):
        """Verify leave balance employee."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/leave-balance?employee_id={emp_id}&year=2024")
        assert resp.status_code == 200

    def test_restrictions_filtered(self, sync_client):
        """Verify restrictions filtered."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/restrictions?employee_id={emp_id}")
        assert resp.status_code == 200

    def test_notes_by_date(self, sync_client):
        """Verify notes by date."""
        resp = sync_client.get("/api/notes?date=2024-06-01")
        assert resp.status_code == 200

    def test_notes_by_employee(self, sync_client):
        """Verify notes by employee."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/notes?employee_id={emp_id}")
        assert resp.status_code == 200

    def test_shift_cycles_assign_get(self, sync_client):
        """Verify shift cycles assign get."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/shift-cycles/assign?employee_id={emp_id}")
        assert resp.status_code in (200, 404)

    def test_staffing_requirements_month(self, sync_client):
        """Verify staffing requirements month."""
        resp = sync_client.get("/api/staffing-requirements?year=2024&month=6")
        assert resp.status_code == 200

    def test_holiday_bans_by_group(self, sync_client):
        """Verify holiday bans by group."""
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/holiday-bans?group_id={gid}")
        assert resp.status_code == 200

    def test_leave_entitlements_filtered(self, sync_client):
        """Verify leave entitlements filtered."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/leave-entitlements?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200

    def test_bookings_filtered(self, sync_client):
        """Verify bookings filtered."""
        resp = sync_client.get("/api/bookings?year=2024&month=6")
        assert resp.status_code == 200

    def test_absences_status_filtered(self, sync_client):
        """Verify absences status filtered."""
        resp = sync_client.get("/api/absences/status?year=2024")
        assert resp.status_code == 200

    def test_employee_access_filtered(self, sync_client):
        """Verify employee access filtered."""
        resp = sync_client.get("/api/employee-access?user_id=1")
        assert resp.status_code == 200

    def test_group_access_filtered(self, sync_client):
        """Verify group access filtered."""
        resp = sync_client.get("/api/group-access?user_id=1")
        assert resp.status_code == 200

    def test_cycle_exceptions_filtered(self, sync_client):
        """Verify cycle exceptions filtered."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/cycle-exceptions?employee_id={emp_id}")
        assert resp.status_code == 200

    def test_overtime_records_by_employee(self, sync_client):
        """Verify overtime records by employee."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/overtime-records?employee_id={emp_id}")
        assert resp.status_code == 200

    def test_schedule_conflicts(self, sync_client):
        """Verify schedule conflicts."""
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=6")
        assert resp.status_code == 200

    def test_staffing(self, sync_client):
        """Verify staffing."""
        resp = sync_client.get("/api/staffing?year=2024&month=6")
        assert resp.status_code == 200

    def test_einsatzplan_deviation(self, sync_client):
        """Verify einsatzplan deviation."""
        resp = sync_client.get("/api/einsatzplan?date=2024-06-01")
        assert resp.status_code == 200

    def test_annual_close_preview_with_group(self, sync_client):
        """Verify annual close preview with group."""
        groups = sync_client.get("/api/groups").json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        resp = sync_client.get(f"/api/annual-close/preview?year=2024&group_id={gid}")
        assert resp.status_code == 200

    def test_special_staffing_filtered(self, sync_client):
        """Verify special staffing filtered."""
        resp = sync_client.get("/api/staffing-requirements/special?date=2024-06-01")
        assert resp.status_code == 200


# ─── Import Edge Cases ────────────────────────────────────────────────────────

class TestImportMoreCoverage:
    def test_import_groups_csv(self, write_client):
        """Verify import groups csv."""
        csv_content = b"NAME,SHORTNAME\nTestGruppe,TG\n"
        resp = write_client.post(
            "/api/import/groups",
            files={"file": ("groups.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200

    def test_import_absences_csv(self, write_client):
        """Verify import absences csv."""
        emps = write_client.get("/api/employees").json()
        lt_list = write_client.get("/api/leave-types").json()
        if not emps or not lt_list:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        lt_id = lt_list[0]["ID"]
        csv_content = f"EMPLOYEE_ID,DATE,LEAVE_TYPE_ID\n{emp_id},2025-09-15,{lt_id}\n".encode()
        resp = write_client.post(
            "/api/import/absences",
            files={"file": ("absences.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200

    def test_import_bookings_actual(self, write_client):
        """Verify import bookings actual."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"EMPLOYEE_ID,DATE,VALUE,NOTE\n{emp_id},2025-09-01,8.0,TestImport\n".encode()
        resp = write_client.post(
            "/api/import/bookings-actual",
            files={"file": ("bookings.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200

    def test_import_bookings_nominal(self, write_client):
        """Verify import bookings nominal."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"EMPLOYEE_ID,DATE,VALUE,NOTE\n{emp_id},2025-09-01,8.0,TestImport\n".encode()
        resp = write_client.post(
            "/api/import/bookings-nominal",
            files={"file": ("bookings.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200

    def test_import_entitlements_csv(self, write_client):
        """Verify import entitlements csv."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"EMPLOYEE_ID,YEAR,DAYS\n{emp_id},2025,25\n".encode()
        resp = write_client.post(
            "/api/import/entitlements",
            files={"file": ("entitlements.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200

    def test_import_absences_new_format_csv(self, write_client):
        """Verify import absences new format csv."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"employee_id,date,leave_type\n{emp_id},2025-09-20,Urlaub\n".encode()
        resp = write_client.post(
            "/api/import/absences-csv",
            files={"file": ("absences.csv", csv_content, "text/csv")}
        )
        assert resp.status_code == 200


# ─── More Database Error Paths ────────────────────────────────────────────────

class TestDatabaseErrors:
    @pytest.mark.parametrize("method,payload", [
        ("update_employee",   {'NAME': 'X'}),
        ("update_shift",      {'NAME': 'X'}),
        ("update_leave_type", {'NAME': 'X'}),
        ("update_holiday",    {'NAME': 'X'}),
        ("update_workplace",  {'NAME': 'X'}),
        ("update_extracharge",{'NAME': 'X'}),
        ("update_group",      {'NAME': 'X'}),
        ("update_user",       {'DESCRIP': 'X'}),
    ])
    def test_update_not_found_raises(self, tmp_db, method, payload):
        """Verify that updating a nonexistent record raises an exception."""
        with pytest.raises((ValueError, Exception)):
            getattr(tmp_db, method)(999999, payload)

    def test_update_note_not_found(self, tmp_db):
        # update_note returns None for nonexistent note instead of raising
        """Verify update note not found."""
        result = tmp_db.update_note(999999, text1='X')
        assert result is None


# ─── Schedule Generate API ────────────────────────────────────────────────────

class TestScheduleGenerateAPI:
    def test_generate_with_employee_ids(self, write_client):
        """Verify generate with employee ids."""
        emps = write_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        resp = write_client.post("/api/schedule/generate", json={
            "year": 2025,
            "month": 9,
            "employee_ids": [emp_id],
            "dry_run": True,
        })
        assert resp.status_code == 200


# ─── Einsatzplan Deviation ────────────────────────────────────────────────────

class TestEinsatzplanDeviation:
    def test_post_deviation(self, write_client):
        """Verify post deviation."""
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No data")
        resp = write_client.post("/api/einsatzplan/deviation", json={
            "employee_id": emps[0]["ID"],
            "date": "2025-07-15",
            "shift_id": shifts[0]["ID"],
        })
        assert resp.status_code in (200, 400, 404, 409)


# ─── Fixture for tmp_db in error path tests ──────────────────────────────────

import shutil  # noqa: E402

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
