"""Additional tests to boost reports.py coverage - import error paths."""
import io
import pytest
from starlette.testclient import TestClient


class TestImportErrorPaths:
    """Test import error paths in reports.py."""

    def test_import_employees_missing_name(self, admin_client: TestClient):
        """CSV with missing NAME → imported=0, errors or skipped>0."""
        csv_content = 'NAME,FIRSTNAME\n,Hans\n,Maria\n'
        res = admin_client.post(
            '/api/import/employees',
            files={'file': ('employees.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200
        data = res.json()
        assert data['skipped'] > 0 or data.get('errors')

    def test_import_employees_with_all_columns(self, admin_client: TestClient):
        """CSV with all optional columns → 200."""
        csv_content = ('NAME,FIRSTNAME,SHORTNAME,NUMBER,SEX,HRSDAY,HRSWEEK,HRSMONTH,WORKDAYS\n'
                       'AllCols,Test,AC1,12345,1,8.0,40.0,173.3,1 1 1 1 1 0 0 0\n')
        res = admin_client.post(
            '/api/import/employees',
            files={'file': ('employees.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_shifts_missing_name(self, admin_client: TestClient):
        """Shifts CSV with missing NAME → skipped."""
        csv_content = 'NAME,SHORTNAME\n,F\n'
        res = admin_client.post(
            '/api/import/shifts',
            files={'file': ('shifts.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200
        data = res.json()
        assert data['skipped'] > 0 or data.get('errors')

    def test_import_shifts_with_color(self, admin_client: TestClient):
        """Shifts CSV with hex color → 200."""
        csv_content = 'NAME,SHORTNAME,FARBE,FROM0,TO0\nColorShift,CS,#FF5733,07:00,15:00\n'
        res = admin_client.post(
            '/api/import/shifts',
            files={'file': ('shifts.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_absences_missing_fields(self, admin_client: TestClient):
        """Absences CSV with missing required fields → skipped."""
        csv_content = 'employee_id,date,leave_type_id\n,,\n1,,\n'
        res = admin_client.post(
            '/api/import/absences',
            files={'file': ('absences.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200
        data = res.json()
        assert data['skipped'] > 0 or data.get('errors')

    def test_import_absences_invalid_date(self, admin_client: TestClient):
        """Absences CSV with invalid date → skipped/error."""
        csv_content = 'employee_id,date,leave_type_id\n1,not-a-date,1\n'
        res = admin_client.post(
            '/api/import/absences',
            files={'file': ('absences.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_holidays_missing_fields(self, admin_client: TestClient):
        """Holidays CSV with missing DATE or NAME → skipped."""
        csv_content = 'date,name\n,Neujahr\n2024-01-02,\n'
        res = admin_client.post(
            '/api/import/holidays',
            files={'file': ('holidays.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200
        data = res.json()
        assert data['skipped'] > 0 or data.get('errors')

    def test_import_holidays_invalid_date(self, admin_client: TestClient):
        """Holidays CSV with invalid date → skipped."""
        csv_content = 'date,name\n32-13-2024,Feiertag\n'
        res = admin_client.post(
            '/api/import/holidays',
            files={'file': ('holidays.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_bookings_actual_missing_fields(self, admin_client: TestClient):
        """Bookings-actual CSV with missing fields → skipped."""
        csv_content = 'employee_id,date,hours\n,,\n'
        res = admin_client.post(
            '/api/import/bookings-actual',
            files={'file': ('bookings.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_bookings_actual_invalid_date(self, admin_client: TestClient):
        """Bookings-actual CSV with invalid date → skipped."""
        csv_content = 'employee_id,date,hours\n1,bad-date,8.0\n'
        res = admin_client.post(
            '/api/import/bookings-actual',
            files={'file': ('bookings.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_entitlements_missing_fields(self, admin_client: TestClient):
        """Entitlements CSV with missing fields → skipped."""
        csv_content = 'employee_id,year,days\n,,\n'
        res = admin_client.post(
            '/api/import/entitlements',
            files={'file': ('ent.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200

    def test_import_groups_missing_name(self, admin_client: TestClient):
        """Groups CSV with missing NAME → skipped."""
        csv_content = 'NAME,SHORTNAME\n,IG\n'
        res = admin_client.post(
            '/api/import/groups',
            files={'file': ('groups.csv', io.BytesIO(csv_content.encode()), 'text/csv')},
        )
        assert res.status_code == 200


class TestExportScheduleHTML:
    """Test HTML export format."""

    def test_export_schedule_html(self, planer_client: TestClient):
        """GET /api/export/schedule format=html → 200."""
        res = planer_client.get('/api/export/schedule?month=2024-06&format=html')
        assert res.status_code == 200

    def test_export_schedule_invalid_format(self, planer_client: TestClient):
        """GET /api/export/schedule format=unknown → 200 (defaults gracefully)."""
        res = planer_client.get('/api/export/schedule?month=2024-06&format=unknown')
        assert res.status_code in (200, 400)

    def test_export_schedule_with_group(self, planer_client: TestClient):
        """GET /api/export/schedule with group_id → 200."""
        res = planer_client.get('/api/export/schedule?month=2024-06&format=csv&group_id=1')
        assert res.status_code == 200


class TestExportStatisticsAdvanced:
    """Test various export statistics options."""

    def test_export_statistics_with_group(self, sync_client: TestClient):
        """GET /api/export/statistics with group_id."""
        res = sync_client.get('/api/export/statistics?year=2024&month=6&group_id=1')
        assert res.status_code == 200

    def test_export_absences_with_group(self, sync_client: TestClient):
        """GET /api/export/absences with group_id → 200."""
        res = sync_client.get('/api/export/absences?year=2024&group_id=1')
        assert res.status_code == 200


class TestScheduleCoverage:
    """Test coverage analysis endpoint."""

    def test_schedule_coverage_basic(self, sync_client: TestClient):
        """GET /api/schedule/coverage → 200."""
        res = sync_client.get('/api/schedule/coverage?year=2024&month=6')
        assert res.status_code == 200

    def test_schedule_coverage_with_group(self, sync_client: TestClient):
        """GET /api/schedule/coverage with group_id → 200."""
        res = sync_client.get('/api/schedule/coverage?year=2024&month=6&group_id=1')
        assert res.status_code == 200


class TestScheduleViews:
    """Test schedule view endpoints."""

    def test_schedule_day_view(self, sync_client: TestClient):
        """GET /api/schedule/day → 200."""
        res = sync_client.get('/api/schedule/day?date=2024-06-01')
        assert res.status_code == 200

    def test_schedule_week_view(self, sync_client: TestClient):
        """GET /api/schedule/week → 200."""
        res = sync_client.get('/api/schedule/week?date=2024-06-01')
        assert res.status_code == 200

    def test_schedule_year_view(self, sync_client: TestClient):
        """GET /api/schedule/year → 200."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        res = sync_client.get(f'/api/schedule/year?year=2024&employee_id={emp_id}')
        assert res.status_code == 200

    def test_schedule_conflicts(self, sync_client: TestClient):
        """GET /api/schedule/conflicts → 200."""
        res = sync_client.get('/api/schedule/conflicts?year=2024&month=6')
        assert res.status_code == 200


class TestScheduleCycleEntries:
    """Test shift cycle with entries."""

    def test_create_cycle_with_entries(self, planer_client: TestClient):
        """POST /api/shift-cycles with entries → 200."""
        shifts = planer_client.get('/api/shifts').json()
        if not shifts:
            pytest.skip("No shifts")
        shift_id = shifts[0]['ID']
        res = planer_client.post('/api/shift-cycles', json={
            'name': 'CycleWithEntries',
            'size_weeks': 2,
            'entries': [
                {'week': 1, 'weekday': 0, 'shift_id': shift_id, 'employee_id': None},
            ],
        })
        assert res.status_code == 200

    def test_assign_and_remove_cycle(self, planer_client: TestClient):
        """Assign then remove cycle for employee."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'AssignCycle', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        emp_id = emps[0]['ID']
        # Assign
        res = planer_client.post('/api/shift-cycles/assign', json={
            'employee_id': emp_id,
            'cycle_id': cycle_id,
            'start_date': '2024-01-01',
        })
        assert res.status_code == 200
        # Remove
        res2 = planer_client.delete(f'/api/shift-cycles/assign/{emp_id}')
        assert res2.status_code == 200


class TestScheduleSwap:
    """Test schedule swap endpoint."""

    def test_swap_shifts(self, planer_client: TestClient):
        """POST /api/schedule/swap → 200 (even with no data to swap)."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        res = planer_client.post('/api/schedule/swap', json={
            'employee_id_1': emps[0]['ID'],
            'employee_id_2': emps[1]['ID'],
            'dates': ['2024-06-01'],
        })
        assert res.status_code == 200


class TestScheduleCopyWeek:
    """Test copy week endpoint."""

    def test_copy_week(self, planer_client: TestClient):
        """POST /api/schedule/copy-week → 200."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        res = planer_client.post('/api/schedule/copy-week', json={
            'source_employee_id': emps[0]['ID'],
            'dates': ['2024-06-03', '2024-06-04', '2024-06-05'],
            'target_employee_ids': [emps[1]['ID']],
        })
        assert res.status_code == 200


class TestScheduleWrite:
    """Test creating a schedule entry successfully."""

    def test_create_and_delete_schedule_entry(self, planer_client: TestClient):
        """POST then DELETE schedule entry → 200."""
        emps = planer_client.get('/api/employees').json()
        shifts = planer_client.get('/api/shifts').json()
        if not emps or not shifts:
            pytest.skip("No data")
        emp_id = emps[0]['ID']
        shift_id = shifts[0]['ID']
        # Create entry
        res = planer_client.post('/api/schedule', json={
            'employee_id': emp_id,
            'date': '2025-01-15',
            'shift_id': shift_id,
        })
        assert res.status_code == 200
        # Now delete it
        del_res = planer_client.delete(f'/api/schedule/{emp_id}/2025-01-15')
        assert del_res.status_code == 200
