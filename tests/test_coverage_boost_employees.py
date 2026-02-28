"""Tests targeting uncovered lines in employees.py to boost coverage."""
import io
import pytest
from starlette.testclient import TestClient


class TestEmployeeValidation:
    """Test Pydantic validation edge cases."""

    def test_create_employee_blank_name_returns_422(self, admin_client: TestClient):
        """NAME with only spaces → 422."""
        res = admin_client.post('/api/employees', json={'NAME': '   '})
        assert res.status_code == 422

    def test_create_employee_invalid_date_birthday(self, admin_client: TestClient):
        """Invalid BIRTHDAY format → 422."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'Test',
            'BIRTHDAY': 'not-a-date',
        })
        assert res.status_code == 422

    def test_create_employee_invalid_date_format(self, admin_client: TestClient):
        """EMPSTART in wrong format → 422."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'Test',
            'EMPSTART': '28.02.2024',
        })
        assert res.status_code == 422

    def test_create_employee_empend_before_empstart(self, admin_client: TestClient):
        """EMPEND < EMPSTART → 422."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'Test',
            'EMPSTART': '2024-06-01',
            'EMPEND': '2024-01-01',
        })
        assert res.status_code == 422

    def test_create_employee_invalid_date_value(self, admin_client: TestClient):
        """BIRTHDAY with valid format but invalid date → 422."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'Test',
            'BIRTHDAY': '2024-13-01',
        })
        assert res.status_code == 422

    def test_update_employee_blank_name_returns_422(self, admin_client: TestClient):
        """PUT /api/employees/{id} with blank NAME → 422."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        res2 = admin_client.put(f'/api/employees/{emp_id}', json={'NAME': '  '})
        assert res2.status_code == 422

    def test_update_employee_invalid_date(self, admin_client: TestClient):
        """PUT with invalid BIRTHDAY → 422."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        res2 = admin_client.put(f'/api/employees/{emp_id}', json={'BIRTHDAY': 'bad-date'})
        assert res2.status_code == 422

    def test_create_employee_with_valid_dates(self, admin_client: TestClient):
        """Create employee with valid date fields."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'DateTest',
            'EMPSTART': '2023-01-01',
            'EMPEND': '2025-12-31',
            'BIRTHDAY': '1990-05-15',
        })
        assert res.status_code == 200

    def test_create_employee_none_birthday(self, admin_client: TestClient):
        """Create employee with None birthday (allowed)."""
        res = admin_client.post('/api/employees', json={
            'NAME': 'NullDate',
            'BIRTHDAY': None,
        })
        assert res.status_code == 200


class TestEmployeeWriteErrorPaths:
    """Test error paths in employee CRUD."""

    def test_create_employee_duplicate_shortname(self, admin_client: TestClient):
        """Create two employees with same SHORTNAME → 409 on second."""
        shortname = 'XDUP1'
        admin_client.post('/api/employees', json={'NAME': 'Dup1', 'SHORTNAME': shortname})
        res2 = admin_client.post('/api/employees', json={'NAME': 'Dup2', 'SHORTNAME': shortname})
        assert res2.status_code == 409
        assert 'vergeben' in res2.json().get('detail', '')

    def test_delete_employee_not_found(self, admin_client: TestClient):
        """DELETE /api/employees/99999 → 404."""
        res = admin_client.delete('/api/employees/99999')
        assert res.status_code == 404

    def test_update_employee_not_found(self, admin_client: TestClient):
        """PUT /api/employees/99999 → 404."""
        res = admin_client.put('/api/employees/99999', json={'NAME': 'Ghost'})
        assert res.status_code == 404

    def test_delete_employee_success(self, admin_client: TestClient):
        """DELETE existing employee → 200."""
        # Create one first
        res = admin_client.post('/api/employees', json={'NAME': 'ToDelete'})
        assert res.status_code == 200
        emp_id = res.json()['record']['ID']
        del_res = admin_client.delete(f'/api/employees/{emp_id}')
        assert del_res.status_code == 200
        assert del_res.json()['ok'] is True


class TestEmployeePhoto:
    """Test photo upload/get."""

    def test_get_photo_not_found(self, sync_client: TestClient):
        """GET /api/employees/99999/photo → 404."""
        res = sync_client.get('/api/employees/99999/photo')
        assert res.status_code == 404

    def test_upload_photo_employee_not_found(self, admin_client: TestClient):
        """POST photo for nonexistent employee → 404."""
        content = b'\xff\xd8\xff' + b'\x00' * 10  # minimal JPEG header
        res = admin_client.post(
            '/api/employees/99999/photo',
            files={'file': ('photo.jpg', io.BytesIO(content), 'image/jpeg')},
        )
        assert res.status_code == 404

    def test_upload_photo_invalid_content_type(self, admin_client: TestClient):
        """POST photo with PDF → 400."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        res2 = admin_client.post(
            f'/api/employees/{emp_id}/photo',
            files={'file': ('file.pdf', io.BytesIO(b'%PDF-1'), 'application/pdf')},
        )
        assert res2.status_code == 400

    def test_upload_photo_too_large(self, admin_client: TestClient):
        """POST photo > 5MB → 413."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        big_content = b'\xff\xd8\xff' + b'\x00' * (5 * 1024 * 1024 + 1)
        res2 = admin_client.post(
            f'/api/employees/{emp_id}/photo',
            files={'file': ('photo.jpg', io.BytesIO(big_content), 'image/jpeg')},
        )
        assert res2.status_code == 413

    def test_upload_photo_success_jpeg(self, admin_client: TestClient):
        """POST valid JPEG photo → 200."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        content = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        res2 = admin_client.post(
            f'/api/employees/{emp_id}/photo',
            files={'file': ('photo.jpg', io.BytesIO(content), 'image/jpeg')},
        )
        assert res2.status_code == 200
        assert res2.json()['ok'] is True

    def test_upload_photo_success_png(self, admin_client: TestClient):
        """POST valid PNG photo → 200."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        res2 = admin_client.post(
            f'/api/employees/{emp_id}/photo',
            files={'file': ('photo.png', io.BytesIO(content), 'image/png')},
        )
        assert res2.status_code == 200

    def test_upload_photo_success_gif(self, admin_client: TestClient):
        """POST valid GIF photo → 200."""
        res = admin_client.get('/api/employees')
        employees = res.json()
        if not employees:
            pytest.skip("No employees in DB")
        emp_id = employees[0]['ID']
        content = b'GIF89a' + b'\x00' * 100
        res2 = admin_client.post(
            f'/api/employees/{emp_id}/photo',
            files={'file': ('photo.gif', io.BytesIO(content), 'image/gif')},
        )
        assert res2.status_code == 200


class TestGroupWriteOps:
    """Test group CRUD and member management."""

    def test_create_group_success(self, admin_client: TestClient):
        """POST /api/groups → 200."""
        res = admin_client.post('/api/groups', json={'NAME': 'TestGrp1', 'SHORTNAME': 'TG1'})
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_create_group_empty_name_returns_400(self, admin_client: TestClient):
        """POST /api/groups with empty NAME → 400."""
        res = admin_client.post('/api/groups', json={'NAME': '  '})
        assert res.status_code == 400

    def test_update_group_success(self, admin_client: TestClient):
        """PUT /api/groups/{id} → 200."""
        # Create first
        create_res = admin_client.post('/api/groups', json={'NAME': 'UpdGrp'})
        grp_id = create_res.json()['record']['ID']
        res = admin_client.put(f'/api/groups/{grp_id}', json={'NAME': 'UpdatedGrp'})
        assert res.status_code == 200

    def test_update_group_not_found(self, admin_client: TestClient):
        """PUT /api/groups/99999 → 404."""
        res = admin_client.put('/api/groups/99999', json={'NAME': 'Ghost'})
        assert res.status_code == 404

    def test_delete_group_success(self, admin_client: TestClient):
        """DELETE /api/groups/{id} → 200."""
        create_res = admin_client.post('/api/groups', json={'NAME': 'DelGrp'})
        assert create_res.status_code == 200
        grp_id = create_res.json()['record']['ID']
        res = admin_client.delete(f'/api/groups/{grp_id}')
        assert res.status_code == 200

    def test_add_group_member(self, admin_client: TestClient):
        """POST /api/groups/{id}/members → 200."""
        # Get an employee
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        # Create a group
        grp = admin_client.post('/api/groups', json={'NAME': 'MemberGrp'})
        grp_id = grp.json()['record']['ID']
        res = admin_client.post(f'/api/groups/{grp_id}/members', json={'employee_id': emp_id})
        assert res.status_code == 200

    def test_remove_group_member(self, admin_client: TestClient):
        """DELETE /api/groups/{id}/members/{emp_id} → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        grp = admin_client.post('/api/groups', json={'NAME': 'RemGrp'})
        grp_id = grp.json()['record']['ID']
        admin_client.post(f'/api/groups/{grp_id}/members', json={'employee_id': emp_id})
        res = admin_client.delete(f'/api/groups/{grp_id}/members/{emp_id}')
        assert res.status_code == 200


class TestBulkEmployeeAction:
    """Test bulk employee operations."""

    def test_bulk_hide_employees(self, admin_client: TestClient):
        """Bulk hide action → 200."""
        # Create employees to hide
        r1 = admin_client.post('/api/employees', json={'NAME': 'BulkHide1'})
        r2 = admin_client.post('/api/employees', json={'NAME': 'BulkHide2'})
        ids = [r1.json()['record']['ID'], r2.json()['record']['ID']]
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': ids,
            'action': 'hide',
        })
        assert res.status_code == 200
        assert res.json()['affected'] == 2

    def test_bulk_show_employees(self, admin_client: TestClient):
        """Bulk show action → 200."""
        r1 = admin_client.post('/api/employees', json={'NAME': 'BulkShow1'})
        ids = [r1.json()['record']['ID']]
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': ids,
            'action': 'show',
        })
        assert res.status_code == 200

    def test_bulk_assign_group(self, admin_client: TestClient):
        """Bulk assign_group → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        grp = admin_client.post('/api/groups', json={'NAME': 'BulkGrp'})
        grp_id = grp.json()['record']['ID']
        ids = [emps[0]['ID']]
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': ids,
            'action': 'assign_group',
            'group_id': grp_id,
        })
        assert res.status_code == 200

    def test_bulk_assign_group_no_group_id(self, admin_client: TestClient):
        """Bulk assign_group without group_id → 400."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [emps[0]['ID']],
            'action': 'assign_group',
        })
        assert res.status_code == 400

    def test_bulk_remove_group(self, admin_client: TestClient):
        """Bulk remove_group → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        grp = admin_client.post('/api/groups', json={'NAME': 'BulkRemGrp'})
        grp_id = grp.json()['record']['ID']
        admin_client.post(f'/api/groups/{grp_id}/members', json={'employee_id': emps[0]['ID']})
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [emps[0]['ID']],
            'action': 'remove_group',
            'group_id': grp_id,
        })
        assert res.status_code == 200

    def test_bulk_remove_group_no_group_id(self, admin_client: TestClient):
        """Bulk remove_group without group_id → 400."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [emps[0]['ID']],
            'action': 'remove_group',
        })
        assert res.status_code == 400

    def test_bulk_unknown_action(self, admin_client: TestClient):
        """Bulk with unknown action → 400."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [emps[0]['ID']],
            'action': 'do_something_weird',
        })
        assert res.status_code == 400

    def test_bulk_empty_list_returns_422(self, admin_client: TestClient):
        """Bulk with empty employee_ids → 422."""
        res = admin_client.post('/api/employees/bulk', json={
            'employee_ids': [],
            'action': 'hide',
        })
        assert res.status_code == 422
