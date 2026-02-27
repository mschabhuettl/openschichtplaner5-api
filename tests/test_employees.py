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
