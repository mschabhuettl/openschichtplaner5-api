"""
API integration tests for OpenSchichtplaner5 backend.

Uses a real copy of the SP5 database (read-only tests use a shared session-copy,
write tests each get a fresh copy via write_client fixture).
"""
import pytest


# ─────────────────────────────────────────────────────────────
# READ TESTS (session-scoped client, shared DB copy)
# ─────────────────────────────────────────────────────────────

class TestRoot:
    def test_api_root(self, sync_client):
        resp = sync_client.get("/api")
        assert resp.status_code == 200
        data = resp.json()
        assert "service" in data
        assert data["service"] == "OpenSchichtplaner5 API"
        assert "version" in data


class TestEmployees:
    def test_list_employees(self, sync_client):
        resp = sync_client.get("/api/employees")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_employee_has_required_fields(self, sync_client):
        resp = sync_client.get("/api/employees")
        emp = resp.json()[0]
        for field in ("ID", "NAME", "SHORTNAME"):
            assert field in emp, f"Missing field: {field}"

    def test_get_employee_by_id(self, sync_client):
        # Get first employee's ID
        emps = sync_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/employees/{emp_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ID"] == emp_id

    def test_get_employee_not_found(self, sync_client):
        resp = sync_client.get("/api/employees/999999")
        assert resp.status_code == 404

    def test_list_employees_include_hidden(self, sync_client):
        resp = sync_client.get("/api/employees?include_hidden=true")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestShifts:
    def test_list_shifts(self, sync_client):
        resp = sync_client.get("/api/shifts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_shift_has_required_fields(self, sync_client):
        shift = sync_client.get("/api/shifts").json()[0]
        for field in ("ID", "NAME"):
            assert field in shift, f"Missing field: {field}"


class TestGroups:
    def test_list_groups(self, sync_client):
        resp = sync_client.get("/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_group_has_required_fields(self, sync_client):
        group = sync_client.get("/api/groups").json()[0]
        for field in ("ID", "NAME"):
            assert field in group, f"Missing field: {field}"

    def test_group_member_count_field(self, sync_client):
        group = sync_client.get("/api/groups").json()[0]
        assert "member_count" in group

    def test_get_group_members(self, sync_client):
        groups = sync_client.get("/api/groups").json()
        group_id = groups[0]["ID"]
        resp = sync_client.get(f"/api/groups/{group_id}/members")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestSchedule:
    def test_get_schedule(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.json()
        # Schedule returns a dict (keyed by employee) or a list
        assert data is not None

    def test_get_schedule_invalid_month(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024&month=13")
        assert resp.status_code == 400

    def test_get_schedule_day(self, sync_client):
        resp = sync_client.get("/api/schedule/day?date=2024-06-01")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_schedule_week(self, sync_client):
        resp = sync_client.get("/api/schedule/week?date=2024-06-03")
        assert resp.status_code == 200

    def test_get_schedule_conflicts(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert "conflicts" in data
        assert isinstance(data["conflicts"], list)

    def test_get_schedule_year(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/schedule/year?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200


class TestHolidays:
    def test_list_holidays(self, sync_client):
        resp = sync_client.get("/api/holidays")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_holidays_with_year(self, sync_client):
        resp = sync_client.get("/api/holidays?year=2024")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestLeaveTypes:
    def test_list_leave_types(self, sync_client):
        resp = sync_client.get("/api/leave-types")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_leave_type_has_fields(self, sync_client):
        lt_list = sync_client.get("/api/leave-types").json()
        if lt_list:
            lt = lt_list[0]
            assert "ID" in lt
            assert "NAME" in lt


class TestWorkplaces:
    def test_list_workplaces(self, sync_client):
        resp = sync_client.get("/api/workplaces")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestStatistics:
    def test_get_statistics(self, sync_client):
        resp = sync_client.get("/api/statistics?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_statistics_invalid_month(self, sync_client):
        resp = sync_client.get("/api/statistics?year=2024&month=0")
        assert resp.status_code == 400

    def test_statistics_has_fields(self, sync_client):
        data = sync_client.get("/api/statistics?year=2024&month=6").json()
        if data:
            stat = data[0]
            for field in ("employee_name", "target_hours", "actual_hours", "overtime_hours"):
                assert field in stat, f"Missing field: {field}"


class TestDashboard:
    def test_dashboard_summary(self, sync_client):
        resp = sync_client.get("/api/dashboard/summary?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert "employees" in data
        assert "shifts_today" in data
        assert "month_label" in data

    def test_dashboard_today(self, sync_client):
        resp = sync_client.get("/api/dashboard/today")
        assert resp.status_code == 200
        data = resp.json()
        assert "date" in data
        assert "on_duty" in data
        assert "absences" in data

    def test_dashboard_upcoming(self, sync_client):
        resp = sync_client.get("/api/dashboard/upcoming")
        assert resp.status_code == 200
        data = resp.json()
        assert "holidays" in data
        assert "birthdays_this_week" in data

    def test_dashboard_stats(self, sync_client):
        resp = sync_client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_employees" in data
        assert "shifts_this_month" in data


class TestAbsences:
    def test_list_absences(self, sync_client):
        resp = sync_client.get("/api/absences")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_absences_filter_by_year(self, sync_client):
        resp = sync_client.get("/api/absences?year=2024")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestZeitkonto:
    def test_zeitkonto(self, sync_client):
        resp = sync_client.get("/api/zeitkonto?year=2024")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_zeitkonto_summary(self, sync_client):
        resp = sync_client.get("/api/zeitkonto/summary?year=2024")
        assert resp.status_code == 200
        data = resp.json()
        assert "year" in data
        assert "employee_count" in data

    def test_zeitkonto_detail(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/zeitkonto/detail?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200


class TestShiftCycles:
    def test_list_cycles(self, sync_client):
        resp = sync_client.get("/api/shift-cycles")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_cycle_assignments(self, sync_client):
        resp = sync_client.get("/api/shift-cycles/assign")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestStaffing:
    def test_staffing_requirements(self, sync_client):
        resp = sync_client.get("/api/staffing-requirements")
        assert resp.status_code == 200

    def test_staffing_with_month(self, sync_client):
        resp = sync_client.get("/api/staffing?year=2024&month=6")
        assert resp.status_code == 200


class TestNotes:
    def test_list_notes(self, sync_client):
        resp = sync_client.get("/api/notes")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestBookings:
    def test_list_bookings(self, sync_client):
        resp = sync_client.get("/api/bookings")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestSettings:
    def test_get_settings(self, sync_client):
        resp = sync_client.get("/api/settings")
        assert resp.status_code == 200


class TestExtraCharges:
    def test_list_extracharges(self, sync_client):
        resp = sync_client.get("/api/extracharges")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestUsers:
    def test_list_users(self, sync_client):
        resp = sync_client.get("/api/users")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestRestrictions:
    def test_list_restrictions(self, sync_client):
        resp = sync_client.get("/api/restrictions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestOvertimeSummary:
    def test_overtime_summary(self, sync_client):
        resp = sync_client.get("/api/overtime-summary?year=2024")
        assert resp.status_code == 200
        data = resp.json()
        assert "year" in data
        assert "employees" in data
        assert "summary" in data


class TestLeaveBalance:
    def test_leave_balance(self, sync_client):
        emps = sync_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = sync_client.get(f"/api/leave-balance?year=2024&employee_id={emp_id}")
        assert resp.status_code == 200


class TestChangelog:
    def test_get_changelog(self, sync_client):
        resp = sync_client.get("/api/changelog")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


# ─────────────────────────────────────────────────────────────
# WRITE TESTS (function-scoped client, fresh DB copy per test)
# ─────────────────────────────────────────────────────────────

class TestEmployeeCreate:
    def test_create_employee(self, write_client):
        payload = {
            "NAME": "Testmann",
            "FIRSTNAME": "Hans",
            "SHORTNAME": "THa",
        }
        resp = write_client.post("/api/employees", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "record" in data
        emp = data["record"]
        assert emp["NAME"] == "Testmann"
        assert emp["FIRSTNAME"] == "Hans"

    def test_create_employee_missing_name(self, write_client):
        resp = write_client.post("/api/employees", json={"FIRSTNAME": "John"})
        assert resp.status_code in (400, 422)

    def test_create_and_retrieve_employee(self, write_client):
        """Created employee should appear in the list."""
        payload = {"NAME": "Listentest", "FIRSTNAME": "Karl"}
        create_resp = write_client.post("/api/employees", json=payload)
        assert create_resp.status_code == 200
        emp_id = create_resp.json()["record"]["ID"]
        get_resp = write_client.get(f"/api/employees/{emp_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["ID"] == emp_id

    def test_update_employee(self, write_client):
        emps = write_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = write_client.put(f"/api/employees/{emp_id}", json={"NOTE1": "Testnotiz"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_employee(self, write_client):
        # Create then hide
        create = write_client.post("/api/employees", json={"NAME": "ToDelete"})
        emp_id = create.json()["record"]["ID"]
        del_resp = write_client.delete(f"/api/employees/{emp_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True


class TestShiftCreate:
    def test_create_shift(self, write_client):
        payload = {"NAME": "Testschicht", "SHORTNAME": "TS", "DURATION0": 8.0}
        resp = write_client.post("/api/shifts", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["record"]["NAME"] == "Testschicht"

    def test_create_shift_missing_name(self, write_client):
        resp = write_client.post("/api/shifts", json={"SHORTNAME": "X"})
        assert resp.status_code in (400, 422)


class TestGroupCreate:
    def test_create_group(self, write_client):
        resp = write_client.post("/api/groups", json={"NAME": "Testgruppe"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["record"]["NAME"] == "Testgruppe"

    def test_create_group_missing_name(self, write_client):
        resp = write_client.post("/api/groups", json={})
        assert resp.status_code in (400, 422)


class TestScheduleWrite:
    def test_create_schedule_entry(self, write_client):
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        resp = write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": "2025-01-15",
            "shift_id": shift_id
        })
        # Could be 200 (ok) or 409 (conflict if entry exists)
        assert resp.status_code in (200, 409)

    def test_create_schedule_entry_invalid_date(self, write_client):
        resp = write_client.post("/api/schedule", json={
            "employee_id": 1,
            "date": "not-a-date",
            "shift_id": 1
        })
        assert resp.status_code == 400

    def test_delete_schedule_entry(self, write_client):
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        # Create first
        write_client.post("/api/schedule", json={
            "employee_id": emp_id,
            "date": "2025-02-10",
            "shift_id": shift_id
        })
        # Then delete
        resp = write_client.delete(f"/api/schedule/{emp_id}/2025-02-10")
        assert resp.status_code == 200


class TestAbsenceWrite:
    def test_create_absence(self, write_client):
        emps = write_client.get("/api/employees").json()
        lt_list = write_client.get("/api/leave-types").json()
        if not lt_list:
            pytest.skip("No leave types configured")
        emp_id = emps[0]["ID"]
        lt_id = lt_list[0]["ID"]
        resp = write_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-03-10",
            "leave_type_id": lt_id
        })
        assert resp.status_code in (200, 409)

    def test_create_absence_invalid_date(self, write_client):
        resp = write_client.post("/api/absences", json={
            "employee_id": 1,
            "date": "2025-99-01",
            "leave_type_id": 1
        })
        assert resp.status_code in (400, 409, 500)


class TestLeaveTypeWrite:
    def test_create_leave_type(self, write_client):
        resp = write_client.post("/api/leave-types", json={"NAME": "Test-Urlaub", "SHORTNAME": "TU"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestHolidayWrite:
    def test_create_holiday(self, write_client):
        resp = write_client.post("/api/holidays", json={
            "DATE": "2025-12-26",
            "NAME": "Zweiter Weihnachtstag",
            "INTERVAL": 1
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_create_holiday_invalid_date(self, write_client):
        resp = write_client.post("/api/holidays", json={
            "DATE": "not-a-date",
            "NAME": "Test"
        })
        assert resp.status_code == 400


class TestWorkplaceWrite:
    def test_create_workplace(self, write_client):
        resp = write_client.post("/api/workplaces", json={"NAME": "Testort", "SHORTNAME": "TO"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestNoteWrite:
    def test_create_note(self, write_client):
        resp = write_client.post("/api/notes", json={
            "date": "2025-06-01",
            "text": "Test-Notiz"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestBookingWrite:
    def test_create_booking(self, write_client):
        emps = write_client.get("/api/employees").json()
        emp_id = emps[0]["ID"]
        resp = write_client.post("/api/bookings", json={
            "employee_id": emp_id,
            "date": "2024-06-15",
            "type": 0,
            "value": 8.0,
            "note": "Test"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestBulkSchedule:
    def test_bulk_schedule_create(self, write_client):
        emps = write_client.get("/api/employees").json()
        shifts = write_client.get("/api/shifts").json()
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        resp = write_client.post("/api/schedule/bulk", json={
            "entries": [
                {"employee_id": emp_id, "date": "2025-04-07", "shift_id": shift_id},
                {"employee_id": emp_id, "date": "2025-04-08", "shift_id": shift_id},
            ],
            "overwrite": True
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "created" in data


class TestExportEndpoints:
    def test_export_schedule_csv(self, sync_client):
        resp = sync_client.get("/api/export/schedule?month=2024-06&format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_export_employees_csv(self, sync_client):
        resp = sync_client.get("/api/export/employees?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_export_statistics_csv(self, sync_client):
        resp = sync_client.get("/api/export/statistics?year=2024&format=csv")
        assert resp.status_code == 200

    def test_export_absences_csv(self, sync_client):
        resp = sync_client.get("/api/export/absences?year=2024&format=csv")
        assert resp.status_code == 200


class TestValidationErrors:
    """Test that validation / edge cases return proper error codes."""

    def test_schedule_conflicts_invalid_month(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=0")
        assert resp.status_code == 400

    def test_dashboard_summary_invalid_month(self, sync_client):
        resp = sync_client.get("/api/dashboard/summary?year=2024&month=13")
        assert resp.status_code == 400

    def test_schedule_day_invalid_format(self, sync_client):
        resp = sync_client.get("/api/schedule/day?date=not-a-date")
        assert resp.status_code == 400
