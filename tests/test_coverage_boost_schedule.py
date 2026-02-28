"""Tests targeting uncovered lines in schedule.py to boost coverage."""
import pytest
from starlette.testclient import TestClient


class TestShiftCycles:
    """Tests for shift cycle CRUD."""

    def test_get_shift_cycles(self, sync_client: TestClient):
        """GET /api/shift-cycles → 200."""
        res = sync_client.get('/api/shift-cycles')
        assert res.status_code == 200

    def test_create_shift_cycle(self, planer_client: TestClient):
        """POST /api/shift-cycles → 200."""
        res = planer_client.post('/api/shift-cycles', json={
            'name': 'TestZyklus',
            'size_weeks': 2,
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_create_shift_cycle_blank_name(self, planer_client: TestClient):
        """POST /api/shift-cycles with blank name → 400."""
        res = planer_client.post('/api/shift-cycles', json={
            'name': '   ',
            'size_weeks': 1,
        })
        assert res.status_code == 400

    def test_get_single_shift_cycle(self, planer_client: TestClient):
        """GET /api/shift-cycles/{id} → 200."""
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'SingleCycle', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        res = planer_client.get(f'/api/shift-cycles/{cycle_id}')
        assert res.status_code == 200

    def test_update_shift_cycle(self, planer_client: TestClient):
        """PUT /api/shift-cycles/{id} → 200."""
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'UpdCycle', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        res = planer_client.put(f'/api/shift-cycles/{cycle_id}', json={
            'name': 'UpdatedCycle',
            'size_weeks': 2,
            'entries': [],
        })
        assert res.status_code == 200

    def test_update_shift_cycle_blank_name(self, planer_client: TestClient):
        """PUT with blank name → 400."""
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'BlankUpd', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        res = planer_client.put(f'/api/shift-cycles/{cycle_id}', json={
            'name': '  ',
            'size_weeks': 1,
            'entries': [],
        })
        assert res.status_code == 400

    def test_update_shift_cycle_not_found(self, planer_client: TestClient):
        """PUT /api/shift-cycles/99999 → 404."""
        res = planer_client.put('/api/shift-cycles/99999', json={
            'name': 'Ghost',
            'size_weeks': 1,
            'entries': [],
        })
        assert res.status_code == 404

    def test_delete_shift_cycle(self, planer_client: TestClient):
        """DELETE /api/shift-cycles/{id} → 200."""
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'DelCycle', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        res = planer_client.delete(f'/api/shift-cycles/{cycle_id}')
        assert res.status_code == 200

    def test_delete_shift_cycle_not_found(self, planer_client: TestClient):
        """DELETE /api/shift-cycles/99999 → 404."""
        res = planer_client.delete('/api/shift-cycles/99999')
        assert res.status_code == 404


class TestCycleAssignment:
    """Tests for cycle assignment."""

    def test_get_cycle_assignments(self, sync_client: TestClient):
        """GET /api/shift-cycles/assign → 200."""
        res = sync_client.get('/api/shift-cycles/assign')
        assert res.status_code == 200

    def test_assign_cycle_invalid_date(self, planer_client: TestClient):
        """POST /api/shift-cycles/assign with valid format but invalid date → 400."""
        res = planer_client.post('/api/shift-cycles/assign', json={
            'employee_id': 1,
            'cycle_id': 1,
            'start_date': '2024-13-01',  # matches pattern but invalid date
        })
        assert res.status_code == 400

    def test_remove_cycle_assignment(self, planer_client: TestClient):
        """DELETE /api/shift-cycles/assign/{employee_id} → 200."""
        res = planer_client.delete('/api/shift-cycles/assign/99999')
        assert res.status_code == 200


class TestScheduleTemplates:
    """Tests for schedule template operations."""

    def test_list_templates(self, sync_client: TestClient):
        """GET /api/schedule/templates → 200."""
        res = sync_client.get('/api/schedule/templates')
        assert res.status_code == 200

    def test_create_template(self, planer_client: TestClient):
        """POST /api/schedule/templates → 200."""
        res = planer_client.post('/api/schedule/templates', json={
            'name': 'TestTemplate',
            'description': 'test',
            'assignments': [],
        })
        assert res.status_code == 200

    def test_delete_template_not_found(self, planer_client: TestClient):
        """DELETE /api/schedule/templates/99999 → 404."""
        res = planer_client.delete('/api/schedule/templates/99999')
        assert res.status_code == 404

    def test_delete_template_success(self, planer_client: TestClient):
        """DELETE /api/schedule/templates/{id} → 200."""
        create_res = planer_client.post('/api/schedule/templates', json={
            'name': 'DelTemplate',
            'description': '',
            'assignments': [],
        })
        assert create_res.status_code == 200
        tmpl_id = create_res.json()['id']
        res = planer_client.delete(f'/api/schedule/templates/{tmpl_id}')
        assert res.status_code == 200

    def test_apply_template_not_found(self, planer_client: TestClient):
        """POST /api/schedule/templates/99999/apply → 404."""
        res = planer_client.post('/api/schedule/templates/99999/apply', json={
            'target_date': '2024-01-01',
            'force': False,
        })
        assert res.status_code == 404


class TestScheduleWrite:
    """Tests for schedule entry write operations."""

    def test_create_schedule_entry_employee_not_found(self, planer_client: TestClient):
        """POST /api/schedule with invalid employee → 404."""
        # Get a valid shift first
        shifts = planer_client.get('/api/shifts').json()
        if not shifts:
            pytest.skip("No shifts in DB")
        shift_id = shifts[0]['ID']
        res = planer_client.post('/api/schedule', json={
            'employee_id': 99999,
            'date': '2024-06-01',
            'shift_id': shift_id,
        })
        assert res.status_code == 404

    def test_create_schedule_entry_shift_not_found(self, planer_client: TestClient):
        """POST /api/schedule with invalid shift → 404."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees in DB")
        res = planer_client.post('/api/schedule', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-06-01',
            'shift_id': 99999,
        })
        assert res.status_code == 404

    def test_delete_schedule_entry(self, planer_client: TestClient):
        """DELETE /api/schedule/{emp_id}/{date} → 404 if not found."""
        res = planer_client.delete('/api/schedule/99999/2024-06-01')
        assert res.status_code == 404

    def test_delete_shift_only(self, planer_client: TestClient):
        """DELETE /api/schedule-shift/{emp_id}/{date} → 200."""
        res = planer_client.delete('/api/schedule-shift/99999/2024-06-01')
        assert res.status_code == 200


class TestScheduleGenerate:
    """Tests for schedule generation."""

    def test_generate_schedule_invalid_month(self, planer_client: TestClient):
        """POST /api/schedule/generate with invalid month → 422 (Pydantic validation)."""
        res = planer_client.post('/api/schedule/generate', json={
            'year': 2024,
            'month': 13,
        })
        assert res.status_code == 422

    def test_generate_schedule_dry_run(self, planer_client: TestClient):
        """POST /api/schedule/generate dry_run=true → 200."""
        res = planer_client.post('/api/schedule/generate', json={
            'year': 2024,
            'month': 6,
            'dry_run': True,
        })
        assert res.status_code == 200
        data = res.json()
        assert 'created' in data
        assert 'message' in data


class TestRestrictions:
    """Tests for employee restrictions."""

    def test_get_restrictions(self, sync_client: TestClient):
        """GET /api/restrictions → 200."""
        res = sync_client.get('/api/restrictions')
        assert res.status_code == 200

    def test_set_restriction(self, admin_client: TestClient):
        """POST /api/restrictions → 200."""
        emps = admin_client.get('/api/employees').json()
        shifts = admin_client.get('/api/shifts').json()
        if not emps or not shifts:
            pytest.skip("No data")
        res = admin_client.post('/api/restrictions', json={
            'employee_id': emps[0]['ID'],
            'shift_id': shifts[0]['ID'],
            'restriction_type': 'block',
        })
        assert res.status_code == 200

    def test_remove_restriction(self, admin_client: TestClient):
        """DELETE /api/restrictions/{employee_id}/{shift_id} → 200."""
        emps = admin_client.get('/api/employees').json()
        shifts = admin_client.get('/api/shifts').json()
        if not emps or not shifts:
            pytest.skip("No data")
        emp_id = emps[0]['ID']
        shift_id = shifts[0]['ID']
        res = admin_client.delete(f'/api/restrictions/{emp_id}/{shift_id}')
        assert res.status_code == 200


class TestBulkSchedule:
    """Tests for bulk schedule operations."""

    def test_bulk_schedule_delete(self, planer_client: TestClient):
        """POST /api/schedule/bulk → 200."""
        res = planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': 1, 'date': '2024-06-01', 'shift_id': None}],
            'overwrite': True,
        })
        assert res.status_code == 200
