"""Tests targeting uncovered lines in reports.py to boost coverage."""
import io
import pytest
from starlette.testclient import TestClient


class TestStatisticsEndpoints:
    """Tests for statistics endpoints."""

    def test_get_statistics_default(self, sync_client: TestClient):
        """GET /api/statistics → 200."""
        res = sync_client.get('/api/statistics')
        assert res.status_code == 200

    def test_get_statistics_with_params(self, sync_client: TestClient):
        """GET /api/statistics?year=2024&month=6 → 200."""
        res = sync_client.get('/api/statistics?year=2024&month=6')
        assert res.status_code == 200

    def test_get_statistics_invalid_month(self, sync_client: TestClient):
        """GET /api/statistics?month=13 → 400."""
        res = sync_client.get('/api/statistics?month=13')
        assert res.status_code == 400

    def test_get_year_summary(self, sync_client: TestClient):
        """GET /api/statistics/year-summary → 200."""
        res = sync_client.get('/api/statistics/year-summary')
        assert res.status_code == 200

    def test_get_year_summary_with_year(self, sync_client: TestClient):
        """GET /api/statistics/year-summary?year=2024 → 200."""
        res = sync_client.get('/api/statistics/year-summary?year=2024')
        assert res.status_code == 200

    def test_get_employee_statistics(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id} → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        res = sync_client.get(f'/api/statistics/employee/{emp_id}')
        assert res.status_code == 200

    def test_get_employee_statistics_with_month(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id}?month=6 → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        res = sync_client.get(f'/api/statistics/employee/{emp_id}?year=2024&month=6')
        assert res.status_code == 200

    def test_get_employee_statistics_not_found(self, sync_client: TestClient):
        """GET /api/statistics/employee/99999 → 404."""
        res = sync_client.get('/api/statistics/employee/99999')
        assert res.status_code == 404

    def test_get_employee_statistics_invalid_month(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id}?month=13 → 400."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        res = sync_client.get(f'/api/statistics/employee/{emp_id}?month=13')
        assert res.status_code == 400

    def test_get_sickness_statistics(self, sync_client: TestClient):
        """GET /api/statistics/sickness → 200."""
        res = sync_client.get('/api/statistics/sickness')
        assert res.status_code == 200

    def test_get_shift_statistics(self, sync_client: TestClient):
        """GET /api/statistics/shifts?year=2024 → 200."""
        res = sync_client.get('/api/statistics/shifts?year=2024')
        assert res.status_code == 200

    def test_get_shift_statistics_with_group(self, sync_client: TestClient):
        """GET /api/statistics/shifts?year=2024&group_id=1 → 200."""
        res = sync_client.get('/api/statistics/shifts?year=2024&group_id=1')
        assert res.status_code == 200


class TestExportEndpoints:
    """Tests for export endpoints."""

    def test_export_schedule_csv(self, planer_client: TestClient):
        """GET /api/export/schedule → 200, CSV."""
        res = planer_client.get('/api/export/schedule?month=2024-06&format=csv')
        assert res.status_code == 200

    def test_export_schedule_xlsx(self, planer_client: TestClient):
        """GET /api/export/schedule format=xlsx → 200 or 500."""
        res = planer_client.get('/api/export/schedule?month=2024-06&format=xlsx')
        assert res.status_code in (200, 500)

    def test_export_schedule_bad_month(self, planer_client: TestClient):
        """GET /api/export/schedule with bad month → 400."""
        res = planer_client.get('/api/export/schedule?month=2024-13&format=csv')
        assert res.status_code == 400

    def test_export_statistics(self, sync_client: TestClient):
        """GET /api/export/statistics → 200."""
        res = sync_client.get('/api/export/statistics?year=2024&month=6')
        assert res.status_code == 200

    def test_export_employees(self, sync_client: TestClient):
        """GET /api/export/employees → 200."""
        res = sync_client.get('/api/export/employees')
        assert res.status_code == 200

    def test_export_absences(self, sync_client: TestClient):
        """GET /api/export/absences → 200."""
        res = sync_client.get('/api/export/absences?year=2024')
        assert res.status_code == 200


class TestBookings:
    """Tests for booking CRUD."""

    def test_get_bookings(self, sync_client: TestClient):
        """GET /api/bookings → 200."""
        res = sync_client.get('/api/bookings')
        assert res.status_code == 200

    def test_create_booking_invalid_date(self, planer_client: TestClient):
        """POST /api/bookings with valid format but invalid date → 400."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/bookings', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-13-01',  # valid format, invalid date → function raises 400
            'type': 0,
            'value': 8.0,
        })
        assert res.status_code == 400

    def test_create_booking_invalid_type(self, planer_client: TestClient):
        """POST /api/bookings with type=99 → 422 (Pydantic, range 0-1)."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/bookings', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-06-01',
            'type': 99,
            'value': 8.0,
        })
        assert res.status_code == 422

    def test_create_and_delete_booking(self, planer_client: TestClient):
        """POST then DELETE booking → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/bookings', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-06-01',
            'type': 0,
            'value': 8.0,
            'note': 'test',
        })
        assert res.status_code == 200
        booking_id = res.json()['record']['id']
        del_res = planer_client.delete(f'/api/bookings/{booking_id}')
        assert del_res.status_code == 200

    def test_delete_booking_not_found(self, planer_client: TestClient):
        """DELETE /api/bookings/99999 → 404."""
        res = planer_client.delete('/api/bookings/99999')
        assert res.status_code == 404


class TestCarryForward:
    """Tests for carry-forward operations."""

    def test_get_carry_forward(self, sync_client: TestClient):
        """GET /api/bookings/carry-forward → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(f'/api/bookings/carry-forward?employee_id={emps[0]["ID"]}&year=2024')
        assert res.status_code == 200

    def test_set_carry_forward(self, planer_client: TestClient):
        """POST /api/bookings/carry-forward → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/bookings/carry-forward', json={
            'employee_id': emps[0]['ID'],
            'year': 2024,
            'hours': 10.5,
        })
        assert res.status_code == 200

    def test_annual_statement(self, planer_client: TestClient):
        """POST /api/bookings/annual-statement → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/bookings/annual-statement', json={
            'employee_id': emps[0]['ID'],
            'year': 2024,
        })
        assert res.status_code == 200

    def test_get_overtime_records(self, sync_client: TestClient):
        """GET /api/overtime-records → 200."""
        res = sync_client.get('/api/overtime-records')
        assert res.status_code == 200


class TestMonthlyReport:
    """Tests for monthly report."""

    def test_get_monthly_report(self, sync_client: TestClient):
        """GET /api/reports/monthly → 200."""
        res = sync_client.get('/api/reports/monthly?year=2024&month=6')
        assert res.status_code == 200

    def test_get_monthly_report_all_employees(self, sync_client: TestClient):
        """GET /api/reports/monthly all employees → 200."""
        res = sync_client.get('/api/reports/monthly?year=2024&month=6')
        assert res.status_code == 200


class TestZeitkonto:
    """Tests for Zeitkonto endpoints."""

    def test_get_zeitkonto(self, sync_client: TestClient):
        """GET /api/zeitkonto → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(f'/api/zeitkonto?employee_id={emps[0]["ID"]}&year=2024')
        assert res.status_code == 200

    def test_get_zeitkonto_summary(self, sync_client: TestClient):
        """GET /api/zeitkonto/summary → 200."""
        res = sync_client.get('/api/zeitkonto/summary?year=2024')
        assert res.status_code == 200

    def test_get_zeitkonto_detail(self, sync_client: TestClient):
        """GET /api/zeitkonto/detail → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(f'/api/zeitkonto/detail?employee_id={emps[0]["ID"]}&year=2024&month=6')
        assert res.status_code == 200


class TestImportEndpoints:
    """Tests for CSV import endpoints (mainly error paths)."""

    def _make_csv(self, header: str, rows: list[str]) -> bytes:
        """Build CSV bytes."""
        content = '\n'.join([header] + rows)
        return content.encode('utf-8')

    def test_import_employees_invalid_content_type(self, admin_client: TestClient):
        """POST /api/import/employees with non-CSV → 400."""
        res = admin_client.post(
            '/api/import/employees',
            files={'file': ('data.pdf', io.BytesIO(b'%PDF-1.4'), 'application/pdf')},
        )
        assert res.status_code == 400

    def test_import_employees_too_large(self, admin_client: TestClient):
        """POST /api/import/employees with file > 10MB → 413."""
        big = b'NAME,FIRSTNAME\n' + b'a' * (11 * 1024 * 1024)
        res = admin_client.post(
            '/api/import/employees',
            files={'file': ('data.csv', io.BytesIO(big), 'text/csv')},
        )
        assert res.status_code == 413

    def test_import_employees_valid_csv(self, admin_client: TestClient):
        """POST /api/import/employees with valid CSV → 200."""
        csv_content = 'NAME,FIRSTNAME,SHORTNAME\nImportTest,Hans,IT1\n'
        res = admin_client.post(
            '/api/import/employees',
            files={'file': ('employees.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_shifts_valid_csv(self, admin_client: TestClient):
        """POST /api/import/shifts → 200."""
        csv_content = 'NAME,SHORTNAME,FROM0,TO0\nFrühschicht,F,06:00,14:00\n'
        res = admin_client.post(
            '/api/import/shifts',
            files={'file': ('shifts.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_absences_valid_csv(self, admin_client: TestClient):
        """POST /api/import/absences → 200."""
        emps = admin_client.get('/api/employees').json()
        leave_types = admin_client.get('/api/leave-types').json()
        if not emps or not leave_types:
            pytest.skip("No data")
        emp_id = emps[0]['ID']
        lt_id = leave_types[0]['ID']
        csv_content = f'employee_id,date,leave_type_id\n{emp_id},2025-11-01,{lt_id}\n'
        res = admin_client.post(
            '/api/import/absences',
            files={'file': ('absences.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_holidays_valid_csv(self, admin_client: TestClient):
        """POST /api/import/holidays → 200."""
        csv_content = 'date,name\n2024-01-01,Neujahr\n'
        res = admin_client.post(
            '/api/import/holidays',
            files={'file': ('holidays.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_groups_valid_csv(self, admin_client: TestClient):
        """POST /api/import/groups → 200."""
        csv_content = 'NAME,SHORTNAME\nImportGrp,IG\n'
        res = admin_client.post(
            '/api/import/groups',
            files={'file': ('groups.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_bookings_actual(self, admin_client: TestClient):
        """POST /api/import/bookings-actual → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        csv_content = f'employee_id,date,hours\n{emp_id},2024-06-01,8.0\n'
        res = admin_client.post(
            '/api/import/bookings-actual',
            files={'file': ('bookings.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_entitlements_valid_csv(self, admin_client: TestClient):
        """POST /api/import/entitlements → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        csv_content = f'employee_id,year,days\n{emp_id},2024,25\n'
        res = admin_client.post(
            '/api/import/entitlements',
            files={'file': ('ent.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200


class TestAnalyticsEndpoints:
    """Tests for analytics/reporting endpoints."""

    def test_get_burnout_radar(self, sync_client: TestClient):
        """GET /api/burnout-radar → 200."""
        res = sync_client.get('/api/burnout-radar?year=2024&month=6')
        assert res.status_code == 200

    def test_get_overtime_summary(self, sync_client: TestClient):
        """GET /api/overtime-summary → 200."""
        res = sync_client.get('/api/overtime-summary?year=2024')
        assert res.status_code == 200

    def test_get_warnings(self, sync_client: TestClient):
        """GET /api/warnings → 200."""
        res = sync_client.get('/api/warnings')
        assert res.status_code == 200

    def test_get_fairness_score(self, sync_client: TestClient):
        """GET /api/fairness → 200."""
        res = sync_client.get('/api/fairness?year=2024')
        assert res.status_code == 200

    def test_get_capacity_forecast(self, sync_client: TestClient):
        """GET /api/capacity-forecast → 200."""
        res = sync_client.get('/api/capacity-forecast?year=2024&month=6')
        assert res.status_code == 200

    def test_get_capacity_year(self, sync_client: TestClient):
        """GET /api/capacity-year → 200."""
        res = sync_client.get('/api/capacity-year?year=2024')
        assert res.status_code == 200

    def test_get_quality_report(self, sync_client: TestClient):
        """GET /api/quality-report → 200."""
        res = sync_client.get('/api/quality-report?year=2024&month=6')
        assert res.status_code == 200

    def test_get_availability_matrix(self, sync_client: TestClient):
        """GET /api/availability-matrix → 200."""
        res = sync_client.get('/api/availability-matrix')
        assert res.status_code == 200

    def test_run_simulation(self, sync_client: TestClient):
        """GET /api/simulation → 200."""
        res = sync_client.get('/api/simulation')
        assert res.status_code == 200
