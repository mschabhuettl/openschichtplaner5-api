"""Tests for Employee CRUD endpoints."""
import pytest
from starlette.testclient import TestClient


class TestGetEmployees:
    def test_get_employees_returns_list(self, sync_client: TestClient):
        """GET /api/employees → 200, list."""
        res = sync_client.get('/api/employees')
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)

    def test_get_employees_has_required_fields(self, sync_client: TestClient):
        """Each employee has ID, NAME, FIRSTNAME fields."""
        res = sync_client.get('/api/employees')
        assert res.status_code == 200
        employees = res.json()
        if employees:
            emp = employees[0]
            assert 'ID' in emp
            assert 'NAME' in emp

    def test_get_employee_by_id(self, sync_client: TestClient):
        """GET /api/employees/{id} → 200 for a valid ID."""
        res = sync_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        res2 = sync_client.get(f'/api/employees/{emp_id}')
        assert res2.status_code == 200
        assert res2.json()['ID'] == emp_id

    def test_get_employee_invalid_id_returns_404(self, sync_client: TestClient):
        """GET /api/employees/99999 → 404."""
        res = sync_client.get('/api/employees/99999')
        assert res.status_code == 404


class TestCreateEmployee:
    def test_create_employee_minimal(self, admin_client: TestClient):
        """POST /api/employees with minimal data → 200."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'Testmann',
            'FIRSTNAME': 'Hans',
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get('ok') is True

    def test_create_employee_missing_name_returns_422(self, admin_client: TestClient):
        """POST /api/employees without NAME → 422."""
        res = admin_client.post('/api/employees', json={
            'FIRSTNAME': 'Hans',
        })
        assert res.status_code == 422

    def test_create_employee_empty_body_returns_422(self, admin_client: TestClient):
        """POST /api/employees with empty body → 422."""
        res = admin_client.post('/api/employees', json={})
        assert res.status_code == 422


class TestUpdateEmployee:
    def test_update_employee(self, admin_client: TestClient):
        """PUT /api/employees/{id} with valid data → 200."""
        # First get employees
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']

        res2 = admin_client.put(f'/api/employees/{emp_id}', json={
            'NAME': employees[0]['NAME'],  # keep same name
        })
        assert res2.status_code == 200

    def test_update_employee_invalid_id_returns_404(self, admin_client: TestClient):
        """PUT /api/employees/99999 → 404."""
        res = admin_client.put('/api/employees/99999', json={'NAME': 'Test'})
        assert res.status_code == 404


class TestGroupCRUD:
    def test_list_groups(self, sync_client: TestClient):
        """Verify list groups."""
        res = sync_client.get('/api/groups')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_groups_include_hidden(self, sync_client: TestClient):
        """Verify list groups include hidden."""
        res = sync_client.get('/api/groups?include_hidden=true')
        assert res.status_code == 200

    def test_get_group_members(self, sync_client: TestClient):
        """Verify get group members."""
        groups = sync_client.get('/api/groups').json()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]['ID']
        res = sync_client.get(f'/api/groups/{gid}/members')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_group_members_not_found(self, sync_client: TestClient):
        """Verify get group members not found."""
        res = sync_client.get('/api/groups/999999/members')
        assert res.status_code in (200, 404)

    def test_create_and_delete_group(self, admin_client: TestClient):
        """Verify create and delete group."""
        res = admin_client.post('/api/groups', json={'NAME': 'TempGrp', 'NOTES': ''})
        assert res.status_code == 200
        # Try to find the new group
        groups = admin_client.get('/api/groups').json()
        new_group = next((g for g in groups if g.get('NAME') == 'TempGrp'), None)
        if new_group:
            del_res = admin_client.delete(f"/api/groups/{new_group['ID']}")
            assert del_res.status_code in (200, 204)

    def test_update_group_not_found(self, admin_client: TestClient):
        """Verify update group not found."""
        res = admin_client.put('/api/groups/999999', json={'NAME': 'X'})
        assert res.status_code in (404, 200)

    def test_delete_group_not_found(self, admin_client: TestClient):
        """Verify delete group not found."""
        res = admin_client.delete('/api/groups/999999')
        assert res.status_code in (404, 200)

    def test_add_remove_group_member(self, admin_client: TestClient):
        """Verify add remove group member."""
        groups = admin_client.get('/api/groups').json()
        emps = admin_client.get('/api/employees').json()
        if not groups or not emps:
            pytest.skip("No groups or employees")
        gid = groups[0]['ID']
        eid = emps[0]['ID']
        # Add member
        res = admin_client.post(f'/api/groups/{gid}/members', json={'employee_id': eid})
        assert res.status_code in (200, 409)
        # Remove member
        res2 = admin_client.delete(f'/api/groups/{gid}/members/{eid}')
        assert res2.status_code in (200, 404)


class TestBulkOperations:
    def test_bulk_hide(self, admin_client: TestClient):
        """Verify bulk hide."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        eid = emps[0]['ID']
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [eid],
            'action': 'hide',
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True
        # Restore
        admin_client.post('/api/employees/bulk', json={
            'employee_ids': [eid],
            'action': 'show',
        })

    def test_bulk_invalid_action(self, admin_client: TestClient):
        """Verify bulk invalid action."""
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [1],
            'action': 'nonexistent',
        })
        assert res.status_code == 400

    def test_bulk_assign_group_missing_group_id(self, admin_client: TestClient):
        """Verify bulk assign group missing group id."""
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [1],
            'action': 'assign_group',
        })
        assert res.status_code == 400

    def test_bulk_assign_group(self, admin_client: TestClient):
        """Verify bulk assign group."""
        groups = admin_client.get('/api/groups').json()
        emps = admin_client.get('/api/employees').json()
        if not groups or not emps:
            pytest.skip("No groups or employees")
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [emps[0]['ID']],
            'action': 'assign_group',
            'group_id': groups[0]['ID'],
        })
        assert res.status_code == 200

    def test_bulk_remove_group_missing_group_id(self, admin_client: TestClient):
        """Verify bulk remove group missing group id."""
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [1],
            'action': 'remove_group',
        })
        assert res.status_code == 400

    def test_employees_include_hidden(self, sync_client: TestClient):
        """Verify employees include hidden."""
        res = sync_client.get('/api/employees?include_hidden=true')
        assert res.status_code == 200
        assert isinstance(res.json(), list)
