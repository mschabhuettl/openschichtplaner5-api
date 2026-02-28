"""Tests targeting uncovered lines in misc.py, absences.py, auth.py, events.py."""
import pytest
from starlette.testclient import TestClient


class TestNotesCRUD:
    """Tests for notes endpoints."""

    def test_add_note_bad_date(self, planer_client: TestClient):
        """POST /api/notes with invalid date → 422 (Pydantic pattern validation)."""
        res = planer_client.post('/api/notes', json={
            'date': '2024-13-99',  # invalid date → 400
            'text': 'hello',
        })
        assert res.status_code in (400, 422)

    def test_add_note_success(self, planer_client: TestClient):
        """POST /api/notes → 200."""
        res = planer_client.post('/api/notes', json={
            'date': '2024-06-15',
            'text': 'Testnotiz',
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_update_note_not_found(self, planer_client: TestClient):
        """PUT /api/notes/99999 → 404."""
        res = planer_client.put('/api/notes/99999', json={'text': 'updated'})
        assert res.status_code == 404

    def test_update_note_success(self, planer_client: TestClient):
        """POST then PUT note → 200."""
        create_res = planer_client.post('/api/notes', json={
            'date': '2024-07-01',
            'text': 'Original',
        })
        assert create_res.status_code == 200
        note_id = create_res.json()['record']['id']
        upd_res = planer_client.put(f'/api/notes/{note_id}', json={'text': 'Updated'})
        assert upd_res.status_code == 200

    def test_delete_note_success(self, planer_client: TestClient):
        """DELETE /api/notes/{id} → 200."""
        create_res = planer_client.post('/api/notes', json={
            'date': '2024-08-01',
            'text': 'ToDelete',
        })
        assert create_res.status_code == 200
        note_id = create_res.json()['record']['id']
        del_res = planer_client.delete(f'/api/notes/{note_id}')
        assert del_res.status_code == 200


class TestSearch:
    """Tests for global search."""

    def test_search_empty_query(self, sync_client: TestClient):
        """GET /api/search?q= → 200, empty results."""
        res = sync_client.get('/api/search?q=')
        assert res.status_code == 200
        data = res.json()
        assert data['results'] == []

    def test_search_with_query(self, sync_client: TestClient):
        """GET /api/search?q=test → 200."""
        res = sync_client.get('/api/search?q=test')
        assert res.status_code == 200
        assert 'results' in res.json()


class TestEmployeeAccess:
    """Tests for employee access rules."""

    def test_get_employee_access(self, admin_client: TestClient):
        """GET /api/employee-access → 200."""
        res = admin_client.get('/api/employee-access')
        assert res.status_code == 200

    def test_get_employee_access_with_user_id(self, admin_client: TestClient):
        """GET /api/employee-access?user_id=1 → 200."""
        res = admin_client.get('/api/employee-access?user_id=1')
        assert res.status_code == 200

    def test_set_employee_access(self, admin_client: TestClient):
        """POST /api/employee-access → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = admin_client.post('/api/employee-access', json={
            'user_id': 1,
            'employee_id': emps[0]['ID'],
        })
        assert res.status_code == 200

    def test_delete_employee_access(self, admin_client: TestClient):
        """DELETE /api/employee-access/{id} → 200."""
        emps = admin_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        create_res = admin_client.post('/api/employee-access', json={
            'user_id': 1,
            'employee_id': emps[0]['ID'],
        })
        assert create_res.status_code == 200
        access_id = create_res.json().get('ID') or create_res.json().get('id')
        if access_id:
            del_res = admin_client.delete(f'/api/employee-access/{access_id}')
            assert del_res.status_code == 200


class TestGroupAccess:
    """Tests for group access rules."""

    def test_get_group_access(self, admin_client: TestClient):
        """GET /api/group-access → 200."""
        res = admin_client.get('/api/group-access')
        assert res.status_code == 200

    def test_set_and_delete_group_access(self, admin_client: TestClient):
        """POST then DELETE /api/group-access."""
        groups = admin_client.get('/api/groups').json()
        if not groups:
            pytest.skip("No groups")
        res = admin_client.post('/api/group-access', json={
            'user_id': 1,
            'group_id': groups[0]['ID'],
        })
        assert res.status_code == 200


class TestChangelog:
    """Tests for changelog/audit log."""

    def test_get_changelog(self, admin_client: TestClient):
        """GET /api/changelog → 200."""
        res = admin_client.get('/api/changelog')
        assert res.status_code == 200

    def test_log_action(self, planer_client: TestClient):
        """POST /api/changelog → 200."""
        res = planer_client.post('/api/changelog', json={
            'user': 'tester',
            'action': 'TEST',
            'entity': 'test_table',
            'entity_id': 1,
            'details': 'Test log entry',
        })
        assert res.status_code == 200


class TestWishes:
    """Tests for shift wishes."""

    def test_get_wishes(self, sync_client: TestClient):
        """GET /api/wishes → 200."""
        res = sync_client.get('/api/wishes')
        assert res.status_code == 200

    def test_create_wish(self, planer_client: TestClient):
        """POST /api/wishes → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/wishes', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-07-15',
            'wish_type': 'WUNSCH',
        })
        assert res.status_code == 200

    def test_delete_wish(self, planer_client: TestClient):
        """DELETE /api/wishes/{id} → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        create_res = planer_client.post('/api/wishes', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-08-15',
            'wish_type': 'SPERRUNG',
        })
        assert create_res.status_code == 200
        wish_id = create_res.json().get('id') or create_res.json().get('ID')
        if wish_id:
            del_res = planer_client.delete(f'/api/wishes/{wish_id}')
            assert del_res.status_code == 200


class TestHandover:
    """Tests for handover notes."""

    def test_get_handover(self, sync_client: TestClient):
        """GET /api/handover → 200."""
        res = sync_client.get('/api/handover')
        assert res.status_code == 200

    def test_create_handover(self, planer_client: TestClient):
        """POST /api/handover → 200."""
        res = planer_client.post('/api/handover', json={
            'date': '2024-06-01',
            'text': 'Übergabenotiz',
        })
        assert res.status_code == 200

    def test_update_handover(self, planer_client: TestClient):
        """PATCH /api/handover/{id} → 200 or 404."""
        res = planer_client.patch('/api/handover/99999', json={'text': 'updated'})
        assert res.status_code in (200, 404)

    def test_delete_handover(self, planer_client: TestClient):
        """DELETE /api/handover/{id} → 200 or 404."""
        create_res = planer_client.post('/api/handover', json={
            'date': '2024-09-01',
            'text': 'del me',
        })
        if create_res.status_code == 200:
            note_id = (create_res.json().get('id') or
                       create_res.json().get('ID') or '1')
            res = planer_client.delete(f'/api/handover/{note_id}')
            assert res.status_code in (200, 404)


class TestAbsenceErrorPaths:
    """Test absence creation error paths."""

    def test_create_absence_employee_not_found(self, planer_client: TestClient):
        """POST /api/absences with bad employee → 404."""
        leave_types = planer_client.get('/api/leave-types').json()
        if not leave_types:
            pytest.skip("No leave types")
        res = planer_client.post('/api/absences', json={
            'employee_id': 99999,
            'date': '2024-07-01',
            'leave_type_id': leave_types[0]['ID'],
        })
        assert res.status_code == 404

    def test_create_absence_leave_type_not_found(self, planer_client: TestClient):
        """POST /api/absences with bad leave_type → 404."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/absences', json={
            'employee_id': emps[0]['ID'],
            'date': '2024-07-01',
            'leave_type_id': 99999,
        })
        assert res.status_code == 404

    def test_bulk_absence(self, planer_client: TestClient):
        """POST /api/absences/bulk → 200."""
        leave_types = planer_client.get('/api/leave-types').json()
        if not leave_types:
            pytest.skip("No leave types")
        res = planer_client.post('/api/absences/bulk', json={
            'date': '2025-12-01',
            'leave_type_id': leave_types[0]['ID'],
            'employee_ids': None,
        })
        assert res.status_code == 200


class TestAuthErrorPaths:
    """Test auth edge cases."""

    def test_login_invalid_credentials(self, app):
        """POST /api/auth/login with bad creds → 401."""
        from starlette.testclient import TestClient
        with TestClient(app) as client:
            res = client.post('/api/auth/login', json={
                'username': 'nonexistent_user_xyz',
                'password': 'wrongpass',
            })
            assert res.status_code == 401

    def test_access_without_token(self, app):
        """Request without auth token → 401."""
        from starlette.testclient import TestClient
        with TestClient(app) as client:
            res = client.get('/api/employees')
            assert res.status_code == 401

    def test_logout(self, app):
        """POST /api/auth/logout → 200 or 204."""
        # Create a separate token to logout
        import secrets
        from api.main import _sessions
        tok = secrets.token_hex(16)
        _sessions[tok] = {'ID': 998, 'NAME': 'logout_test', 'role': 'Leser', 'ADMIN': False, 'RIGHTS': 1}
        from starlette.testclient import TestClient
        with TestClient(app) as client:
            res = client.post('/api/auth/logout', headers={'X-Auth-Token': tok})
            assert res.status_code in (200, 204)


class TestSwapRequests:
    """Tests for swap request operations."""

    def test_get_swap_requests(self, sync_client: TestClient):
        """GET /api/swap-requests → 200."""
        res = sync_client.get('/api/swap-requests')
        assert res.status_code == 200

    def test_create_swap_request_invalid_date(self, planer_client: TestClient):
        """POST /api/swap-requests with bad date → 400 (function validates)."""
        res = planer_client.post('/api/swap-requests', json={
            'requester_id': 1,
            'requester_date': 'not-a-date',
            'partner_id': 2,
            'partner_date': '2024-06-01',
        })
        assert res.status_code in (400, 422)

    def test_create_swap_request_same_person(self, planer_client: TestClient):
        """POST /api/swap-requests with same requester/partner → 400 (function validates)."""
        res = planer_client.post('/api/swap-requests', json={
            'requester_id': 1,
            'requester_date': '2024-06-01',
            'partner_id': 1,
            'partner_date': '2024-06-02',
        })
        assert res.status_code in (400, 422)

    def test_resolve_swap_request_invalid_action(self, planer_client: TestClient):
        """PATCH /api/swap-requests/1/resolve with bad action → 400 (function validates)."""
        res = planer_client.patch('/api/swap-requests/99999/resolve', json={
            'action': 'invalid_action',
        })
        assert res.status_code in (400, 422)

    def test_resolve_swap_request_not_found(self, planer_client: TestClient):
        """PATCH /api/swap-requests/99999/resolve reject → 404."""
        res = planer_client.patch('/api/swap-requests/99999/resolve', json={
            'action': 'reject',
        })
        assert res.status_code == 404

    def test_delete_swap_request_not_found(self, planer_client: TestClient):
        """DELETE /api/swap-requests/99999 → 404."""
        res = planer_client.delete('/api/swap-requests/99999')
        assert res.status_code == 404


class TestEventsEndpoint:
    """Tests for SSE broadcast function."""

    def test_broadcast_function(self):
        """Test broadcast() doesn't raise errors."""
        from api.routers.events import broadcast
        # With no subscribers, should be a no-op
        broadcast('test_event', {'key': 'value'})
        broadcast('schedule_changed', None)

    def test_broadcast_with_subscriber(self):
        """Test broadcast to a subscriber."""
        import asyncio
        from api.routers.events import broadcast, _subscribers, _lock

        loop = asyncio.new_event_loop()
        queue = asyncio.Queue(maxsize=10)
        with _lock:
            _subscribers.append((loop, queue))
        try:
            broadcast('test_event', {'data': 'hello'})
        finally:
            with _lock:
                try:
                    _subscribers.remove((loop, queue))
                except ValueError:
                    pass
            loop.close()
