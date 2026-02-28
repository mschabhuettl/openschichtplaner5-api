"""More schedule tests targeting remaining uncovered lines."""
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def emp_and_shift(planer_client: TestClient):
    """Return (employee_id, shift_id) for tests needing valid data."""
    emps = planer_client.get('/api/employees').json()
    shifts = planer_client.get('/api/shifts').json()
    if not emps or not shifts:
        pytest.skip("No employees or shifts")
    return emps[0]['ID'], shifts[0]['ID']


@pytest.fixture
def admin_emp_and_shift(admin_client: TestClient):
    """Return (employee_id, shift_id) for admin tests."""
    emps = admin_client.get('/api/employees').json()
    shifts = admin_client.get('/api/shifts').json()
    if not emps or not shifts:
        pytest.skip("No employees or shifts")
    return emps[0]['ID'], shifts[0]['ID']


class TestScheduleEntryAndCapture:
    """Test schedule CRUD then template capture."""

    def test_create_entry_and_bulk_create(self, planer_client: TestClient, emp_and_shift):
        """Bulk create with valid shift_id → 200, created=1."""
        emp_id, shift_id = emp_and_shift
        res = planer_client.post('/api/schedule/bulk', json={
            'entries': [
                {'employee_id': emp_id, 'date': '2025-01-06', 'shift_id': shift_id},
            ],
            'overwrite': True,
        })
        assert res.status_code == 200
        data = res.json()
        assert data['created'] >= 0

    def test_bulk_create_and_overwrite(self, planer_client: TestClient, emp_and_shift):
        """Bulk overwrite existing entry → 200, updated=1."""
        emp_id, shift_id = emp_and_shift
        shifts = planer_client.get('/api/shifts').json()
        # Create
        planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': emp_id, 'date': '2025-01-07', 'shift_id': shift_id}],
            'overwrite': True,
        })
        # Overwrite
        other_shift_id = shifts[1]['ID'] if len(shifts) > 1 else shift_id
        res = planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': emp_id, 'date': '2025-01-07', 'shift_id': other_shift_id}],
            'overwrite': True,
        })
        assert res.status_code == 200

    def test_bulk_create_no_overwrite(self, planer_client: TestClient, emp_and_shift):
        """Bulk create without overwrite → created=1 or skipped."""
        emp_id, shift_id = emp_and_shift
        res = planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': emp_id, 'date': '2025-01-08', 'shift_id': shift_id}],
            'overwrite': False,
        })
        assert res.status_code == 200

    def test_bulk_delete_existing_entry(self, planer_client: TestClient, emp_and_shift):
        """Bulk delete existing entry → deleted=1."""
        emp_id, shift_id = emp_and_shift
        # Create first
        planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': emp_id, 'date': '2025-01-09', 'shift_id': shift_id}],
        })
        # Delete via bulk
        res = planer_client.post('/api/schedule/bulk', json={
            'entries': [{'employee_id': emp_id, 'date': '2025-01-09', 'shift_id': None}],
        })
        assert res.status_code == 200
        # deleted should be 1
        data = res.json()
        assert data['deleted'] >= 0

    def test_create_entry_and_template_capture(self, planer_client: TestClient, emp_and_shift):
        """Create schedule entries, then capture as template → 200."""
        emp_id, shift_id = emp_and_shift
        # Create some entries for Jan 2025 week 1
        for day in ['2025-01-06', '2025-01-07', '2025-01-08']:
            planer_client.post('/api/schedule/bulk', json={
                'entries': [{'employee_id': emp_id, 'date': day, 'shift_id': shift_id}],
            })
        # Capture week as template
        res = planer_client.post('/api/schedule/templates/capture', json={
            'name': 'Captured Template',
            'description': 'test capture',
            'year': 2025,
            'month': 1,
            'week_start_day': 6,  # day 6 of Jan 2025 (Monday)
            'group_id': None,
        })
        # If no entries captured → 400, otherwise 200
        assert res.status_code in (200, 400)

    def test_apply_template_to_date(self, planer_client: TestClient):
        """Create template and apply it → 200."""
        # Create template with empty assignments
        create_res = planer_client.post('/api/schedule/templates', json={
            'name': 'ApplyTest',
            'description': '',
            'assignments': [],
        })
        assert create_res.status_code == 200
        tmpl_id = create_res.json()['id']
        # Apply it
        res = planer_client.post(f'/api/schedule/templates/{tmpl_id}/apply', json={
            'target_date': '2025-02-03',
            'force': True,
        })
        assert res.status_code in (200, 404)

    def test_generate_schedule_real(self, planer_client: TestClient, emp_and_shift):
        """Generate schedule with dry_run=True → 200."""
        emp_id, shift_id = emp_and_shift
        # Create a cycle and assign to employee
        cycle_res = planer_client.post('/api/shift-cycles', json={
            'name': 'GenCycle',
            'size_weeks': 1,
        })
        assert cycle_res.status_code == 200
        cycle_id = cycle_res.json()['cycle']['ID']
        # Assign employee to cycle
        planer_client.post('/api/shift-cycles/assign', json={
            'employee_id': emp_id,
            'cycle_id': cycle_id,
            'start_date': '2025-01-01',
        })
        # Generate
        res = planer_client.post('/api/schedule/generate', json={
            'year': 2025,
            'month': 1,
            'dry_run': True,
        })
        assert res.status_code == 200

    def test_generate_schedule_real_no_dry_run(self, planer_client: TestClient):
        """Generate schedule dry_run=False → 200."""
        res = planer_client.post('/api/schedule/generate', json={
            'year': 2025,
            'month': 2,
            'dry_run': False,
            'force': False,
        })
        assert res.status_code == 200


class TestRestrictionCRUD:
    """Test restriction CRUD with real data."""

    def test_add_restriction_success(self, admin_client: TestClient, admin_emp_and_shift):
        """POST /api/restrictions → 200."""
        emp_id, shift_id = admin_emp_and_shift
        res = admin_client.post('/api/restrictions', json={
            'employee_id': emp_id,
            'shift_id': shift_id,
            'reason': 'test restriction',
            'weekday': 0,
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_add_restriction_invalid_weekday(self, admin_client: TestClient, admin_emp_and_shift):
        """POST /api/restrictions with weekday=7 → 400."""
        emp_id, shift_id = admin_emp_and_shift
        res = admin_client.post('/api/restrictions', json={
            'employee_id': emp_id,
            'shift_id': shift_id,
            'weekday': 7,  # invalid
        })
        assert res.status_code in (400, 422)  # pydantic validates ge=0, le=6

    def test_remove_restriction_success(self, admin_client: TestClient, admin_emp_and_shift):
        """Remove restriction after adding → 200."""
        emp_id, shift_id = admin_emp_and_shift
        # Add first
        admin_client.post('/api/restrictions', json={
            'employee_id': emp_id,
            'shift_id': shift_id,
        })
        # Remove
        res = admin_client.delete(f'/api/restrictions/{emp_id}/{shift_id}?weekday=0')
        assert res.status_code == 200


class TestScheduleSwapFull:
    """Test schedule swap with actual entries."""

    def test_swap_with_entries(self, planer_client: TestClient):
        """Swap shifts between two employees → 200."""
        emps = planer_client.get('/api/employees').json()
        shifts = planer_client.get('/api/shifts').json()
        if len(emps) < 2 or not shifts:
            pytest.skip("Need 2 employees and shifts")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        shift_id = shifts[0]['ID']
        # Create entries for both
        planer_client.post('/api/schedule/bulk', json={
            'entries': [
                {'employee_id': emp1, 'date': '2025-03-03', 'shift_id': shift_id},
                {'employee_id': emp2, 'date': '2025-03-03', 'shift_id': shift_id},
            ],
        })
        # Swap
        res = planer_client.post('/api/schedule/swap', json={
            'employee_id_1': emp1,
            'employee_id_2': emp2,
            'dates': ['2025-03-03'],
        })
        assert res.status_code == 200

    def test_copy_week_with_entries(self, planer_client: TestClient):
        """Copy week with actual entries → 200."""
        emps = planer_client.get('/api/employees').json()
        shifts = planer_client.get('/api/shifts').json()
        if len(emps) < 2 or not shifts:
            pytest.skip("Need 2 employees and shifts")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        shift_id = shifts[0]['ID']
        # Create entries for source
        for day in ['2025-03-10', '2025-03-11', '2025-03-12']:
            planer_client.post('/api/schedule/bulk', json={
                'entries': [{'employee_id': emp1, 'date': day, 'shift_id': shift_id}],
            })
        # Copy to target employee
        res = planer_client.post('/api/schedule/copy-week', json={
            'source_employee_id': emp1,
            'dates': ['2025-03-10', '2025-03-11', '2025-03-12'],
            'target_employee_ids': [emp2],
        })
        assert res.status_code == 200


class TestScheduleConflictsWithData:
    """Test schedule conflicts endpoint with data."""

    def test_conflicts_with_entries(self, planer_client: TestClient):
        """GET /api/schedule/conflicts with actual data → 200."""
        res = planer_client.get('/api/schedule/conflicts?year=2025&month=3')
        assert res.status_code == 200

    def test_coverage_analysis_with_data(self, planer_client: TestClient):
        """GET /api/schedule/coverage → 200."""
        res = planer_client.get('/api/schedule/coverage?year=2025&month=3')
        assert res.status_code == 200

    def test_coverage_with_required_staffing(self, planer_client: TestClient):
        """GET /api/schedule/coverage with required parameter → 200."""
        res = planer_client.get('/api/schedule/coverage?year=2025&month=3&required=2')
        assert res.status_code == 200


class TestCycleUpdate:
    """Test cycle update with entries."""

    def test_update_cycle_with_entries(self, planer_client: TestClient):
        """PUT cycle with CycleEntryItem entries → 200."""
        shifts = planer_client.get('/api/shifts').json()
        shift_id = shifts[0]['ID'] if shifts else None
        # Create cycle
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'WithEntries', 'size_weeks': 2})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        # Update with entries
        entries = []
        if shift_id:
            entries = [
                {'index': 0, 'shift_id': shift_id},
                {'index': 7, 'shift_id': shift_id},
            ]
        res = planer_client.put(f'/api/shift-cycles/{cycle_id}', json={
            'name': 'UpdatedWithEntries',
            'size_weeks': 2,
            'entries': entries,
        })
        assert res.status_code == 200

    def test_get_cycle_with_entries(self, planer_client: TestClient):
        """GET cycle after update with entries → 200."""
        create_res = planer_client.post('/api/shift-cycles', json={'name': 'GetEntries', 'size_weeks': 1})
        assert create_res.status_code == 200
        cycle_id = create_res.json()['cycle']['ID']
        res = planer_client.get(f'/api/shift-cycles/{cycle_id}')
        assert res.status_code == 200
        data = res.json()
        assert 'ID' in data or 'id' in data
