"""Tests for Schedule endpoints (GET, POST, DELETE)."""
import pytest
from starlette.testclient import TestClient


class TestGetSchedule:
    def test_get_schedule_returns_list(self, sync_client: TestClient):
        """GET /api/schedule?year=2026&month=1 → 200, list."""
        res = sync_client.get('/api/schedule?year=2026&month=1')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_schedule_with_group(self, sync_client: TestClient):
        """GET /api/schedule with group_id filter → 200."""
        res = sync_client.get('/api/schedule?year=2026&month=1&group_id=1')
        assert res.status_code in (200, 404)  # 404 if group doesn't exist

    def test_get_schedule_missing_year_returns_422(self, sync_client: TestClient):
        """GET /api/schedule without year → 422."""
        res = sync_client.get('/api/schedule?month=1')
        assert res.status_code == 422

    def test_get_schedule_invalid_month_returns_422(self, sync_client: TestClient):
        """GET /api/schedule with invalid month → 422."""
        res = sync_client.get('/api/schedule?year=2026&month=13')
        assert res.status_code == 422


class TestCreateAndDeleteScheduleEntry:
    def test_post_schedule_entry(self, write_client: TestClient):
        """POST /api/schedule → creates an entry."""
        emps = write_client.get('/api/employees').json()
        shifts = write_client.get('/api/shifts').json()
        if not emps or not shifts:
            pytest.skip("No employees or shifts in DB")

        emp_id = emps[0]['ID']
        shift_id = shifts[0]['ID']

        res = write_client.post('/api/schedule', json={
            'employee_id': emp_id,
            'date': '2026-06-15',
            'shift_id': shift_id,
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get('ok') is True

    def test_post_schedule_invalid_date_returns_422(self, write_client: TestClient):
        """POST /api/schedule with invalid date → 422."""
        res = write_client.post('/api/schedule', json={
            'employee_id': 1,
            'date': 'not-a-date',
            'shift_id': 1,
        })
        assert res.status_code == 422

    def test_delete_schedule_entry(self, write_client: TestClient):
        """DELETE /api/schedule/{emp_id}/{date} → removes entry."""
        emps = write_client.get('/api/employees').json()
        shifts = write_client.get('/api/shifts').json()
        if not emps or not shifts:
            pytest.skip("No employees or shifts in DB")

        emp_id = emps[0]['ID']
        shift_id = shifts[0]['ID']
        test_date = '2026-07-15'

        # Create first
        write_client.post('/api/schedule', json={
            'employee_id': emp_id,
            'date': test_date,
            'shift_id': shift_id,
        })

        # Then delete
        res = write_client.delete(f'/api/schedule/{emp_id}/{test_date}')
        assert res.status_code == 200
        assert res.json().get('ok') is True

    def test_delete_nonexistent_entry_returns_404(self, write_client: TestClient):
        """DELETE /api/schedule on nonexistent entry → 404."""
        res = write_client.delete('/api/schedule/99999/2026-01-01')
        assert res.status_code == 404


class TestScheduleConflicts:
    def test_get_conflicts(self, sync_client: TestClient):
        """GET /api/schedule/conflicts → 200 with conflicts list."""
        res = sync_client.get('/api/schedule/conflicts?year=2026&month=1')
        assert res.status_code == 200
        data = res.json()
        assert 'conflicts' in data
        assert isinstance(data['conflicts'], list)
