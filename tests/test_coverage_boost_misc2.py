"""Additional tests for misc.py coverage - swap requests, search, self-service."""
import pytest
from starlette.testclient import TestClient


class TestSearchFuzzy:
    """Test search with actual employee data to cover fuzzy matching paths."""

    def test_search_exact_match(self, sync_client: TestClient):
        """Search for exact employee name → covers exact match path in _fuzzy_score."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_name = emps[0].get('NAME', '') or ''
        if not emp_name:
            pytest.skip("No employee name")
        res = sync_client.get(f'/api/search?q={emp_name.lower()}')
        assert res.status_code == 200
        # Result list might be non-empty
        assert 'results' in res.json()

    def test_search_starts_with(self, sync_client: TestClient):
        """Search for prefix of employee name → covers starts_with path."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        for emp in emps:
            name = emp.get('NAME', '') or ''
            if len(name) > 2:
                prefix = name[:2]
                res = sync_client.get(f'/api/search?q={prefix}')
                assert res.status_code == 200
                assert 'results' in res.json()
                break

    def test_search_partial_word(self, sync_client: TestClient):
        """Search for partial word that triggers bigram scoring."""
        res = sync_client.get('/api/search?q=er')
        assert res.status_code == 200
        assert 'results' in res.json()

    def test_search_shift_exact(self, sync_client: TestClient):
        """Search for exact shift name → hits shift results."""
        shifts = sync_client.get('/api/shifts').json()
        if not shifts:
            pytest.skip("No shifts")
        shift_name = shifts[0].get('NAME', '') or ''
        if not shift_name:
            pytest.skip("No shift name")
        res = sync_client.get(f'/api/search?q={shift_name.lower()}')
        assert res.status_code == 200

    def test_search_leave_type(self, sync_client: TestClient):
        """Search for leave type name."""
        lts = sync_client.get('/api/leave-types').json()
        if not lts:
            pytest.skip("No leave types")
        lt_name = lts[0].get('NAME', '') or ''
        if not lt_name:
            pytest.skip("No leave type name")
        res = sync_client.get(f'/api/search?q={lt_name.lower()}')
        assert res.status_code == 200

    def test_search_no_results(self, sync_client: TestClient):
        """Search for nonsense → empty results."""
        res = sync_client.get('/api/search?q=zzxzxzxzxzz')
        assert res.status_code == 200
        assert res.json()['results'] == []

    def test_search_shortname(self, sync_client: TestClient):
        """Search for exact shortname → covers exact match path."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        for emp in emps:
            short = emp.get('SHORTNAME', '') or ''
            if short:
                res = sync_client.get(f'/api/search?q={short}')
                assert res.status_code == 200
                break


class TestSwapRequestFlow:
    """Test complete swap request flow."""

    def test_create_swap_request_success(self, planer_client: TestClient):
        """Create swap request with valid employees → 200."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        emp1 = emps[0]['ID']
        emp2 = emps[1]['ID']
        res = planer_client.post('/api/swap-requests', json={
            'requester_id': emp1,
            'requester_date': '2025-04-01',
            'partner_id': emp2,
            'partner_date': '2025-04-02',
            'note': 'Test swap',
        })
        assert res.status_code == 200
        data = res.json()
        assert 'id' in data
        return data['id']

    def test_resolve_swap_reject(self, planer_client: TestClient):
        """Create then reject a swap request → 200."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        # Create request
        create_res = planer_client.post('/api/swap-requests', json={
            'requester_id': emp1,
            'requester_date': '2025-05-01',
            'partner_id': emp2,
            'partner_date': '2025-05-02',
        })
        assert create_res.status_code == 200
        swap_id = create_res.json()['id']
        # Reject it
        res = planer_client.patch(f'/api/swap-requests/{swap_id}/resolve', json={
            'action': 'reject',
            'reject_reason': 'Test rejection',
        })
        assert res.status_code == 200

    def test_resolve_swap_approve(self, planer_client: TestClient):
        """Create then approve a swap request → 200."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        # Create request
        create_res = planer_client.post('/api/swap-requests', json={
            'requester_id': emp1,
            'requester_date': '2025-06-01',
            'partner_id': emp2,
            'partner_date': '2025-06-01',  # same date for simple same-date swap
            'note': 'Approve me',
        })
        assert create_res.status_code == 200
        swap_id = create_res.json()['id']
        # Approve
        res = planer_client.patch(f'/api/swap-requests/{swap_id}/resolve', json={
            'action': 'approve',
            'resolved_by': 'test_planer',
        })
        assert res.status_code == 200

    def test_delete_swap_request_success(self, planer_client: TestClient):
        """Create then delete a swap request → 200."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        create_res = planer_client.post('/api/swap-requests', json={
            'requester_id': emp1,
            'requester_date': '2025-07-01',
            'partner_id': emp2,
            'partner_date': '2025-07-02',
        })
        assert create_res.status_code == 200
        swap_id = create_res.json()['id']
        res = planer_client.delete(f'/api/swap-requests/{swap_id}')
        assert res.status_code == 200

    def test_create_swap_request_invalid_date(self, planer_client: TestClient):
        """Create swap with invalid date format → 400."""
        emps = planer_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        emp1, emp2 = emps[0]['ID'], emps[1]['ID']
        res = planer_client.post('/api/swap-requests', json={
            'requester_id': emp1,
            'requester_date': '2024-13-01',  # invalid date
            'partner_id': emp2,
            'partner_date': '2025-07-02',
        })
        assert res.status_code == 400


class TestWishWrongType:
    """Test wish with wrong type."""

    def test_create_wish_wrong_type(self, planer_client: TestClient):
        """Create wish with invalid type → 400."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/wishes', json={
            'employee_id': emps[0]['ID'],
            'date': '2025-07-15',
            'wish_type': 'INVALID_TYPE',
        })
        assert res.status_code == 400

    def test_create_wish_case_insensitive(self, planer_client: TestClient):
        """Create wish with lowercase type → 200."""
        emps = planer_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post('/api/wishes', json={
            'employee_id': emps[0]['ID'],
            'date': '2025-08-15',
            'wish_type': 'wunsch',  # lowercase → should be uppercased internally
        })
        assert res.status_code == 200

    def test_delete_wish_not_found(self, planer_client: TestClient):
        """Delete non-existent wish → 404."""
        res = planer_client.delete('/api/wishes/99999')
        assert res.status_code == 404


class TestHandoverFilters:
    """Test handover endpoint filters."""

    def test_get_handover_with_shift_filter(self, sync_client: TestClient):
        """GET /api/handover with shift_id filter."""
        res = sync_client.get('/api/handover?shift_id=1')
        assert res.status_code == 200

    def test_get_handover_with_date_filter(self, sync_client: TestClient):
        """GET /api/handover with date filter."""
        res = sync_client.get('/api/handover?date=2025-01-01')
        assert res.status_code == 200

    def test_delete_handover_success(self, planer_client: TestClient):
        """Create then delete handover → 200."""
        create_res = planer_client.post('/api/handover', json={
            'date': '2025-07-01',
            'text': 'To be deleted',
            'shift_id': None,
        })
        assert create_res.status_code == 200
        note_id = create_res.json().get('id')
        if note_id:
            del_res = planer_client.delete(f'/api/handover/{note_id}')
            assert del_res.status_code in (200, 404)

    def test_update_handover_success(self, planer_client: TestClient):
        """Create then update handover note."""
        create_res = planer_client.post('/api/handover', json={
            'date': '2025-07-02',
            'text': 'Original handover',
        })
        assert create_res.status_code == 200
        note_id = create_res.json().get('id')
        if note_id:
            upd_res = planer_client.patch(f'/api/handover/{note_id}', json={'text': 'Updated handover'})
            assert upd_res.status_code in (200, 404)


class TestNoteWithDate:
    """Test note update with date validation."""

    def test_update_note_with_valid_date(self, planer_client: TestClient):
        """PUT /api/notes/{id} with valid date change → 200."""
        # Create note
        create_res = planer_client.post('/api/notes', json={
            'date': '2025-09-01',
            'text': 'Original',
        })
        assert create_res.status_code == 200
        note_id = create_res.json()['record']['id']
        # Update with new date
        upd_res = planer_client.put(f'/api/notes/{note_id}', json={
            'date': '2025-09-15',
            'text': 'Updated with date',
        })
        assert upd_res.status_code == 200

    def test_update_note_with_invalid_date_format(self, planer_client: TestClient):
        """PUT /api/notes/{id} with invalid date → 400."""
        # Create note
        create_res = planer_client.post('/api/notes', json={
            'date': '2025-09-01',
            'text': 'Test',
        })
        assert create_res.status_code == 200
        note_id = create_res.json()['record']['id']
        # Update with invalid date (matches pattern but bad value)
        upd_res = planer_client.put(f'/api/notes/{note_id}', json={'date': '2025-13-99'})
        assert upd_res.status_code == 400
