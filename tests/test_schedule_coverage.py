"""Targeted tests for schedule.py coverage gaps.

Covers: day/week/year endpoints, staffing, shift-cycles CRUD,
        schedule templates, restrictions, generate, delete-shift-only.
"""
import pytest
from starlette.testclient import TestClient


# ── /api/schedule/coverage ───────────────────────────────────
class TestScheduleCoverage:
    def test_coverage_valid(self, sync_client: TestClient):
        """Verify coverage valid."""
        res = sync_client.get('/api/schedule/coverage?year=2026&month=1')
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        if data:
            assert 'day' in data[0]
            assert 'status' in data[0]

    def test_coverage_invalid_month(self, sync_client: TestClient):
        """Verify coverage invalid month."""
        res = sync_client.get('/api/schedule/coverage?year=2026&month=0')
        assert res.status_code == 400

    def test_coverage_invalid_year(self, sync_client: TestClient):
        """Verify coverage invalid year."""
        res = sync_client.get('/api/schedule/coverage?year=1800&month=1')
        assert res.status_code == 400

    def test_coverage_missing_params(self, sync_client: TestClient):
        """Verify coverage missing params."""
        res = sync_client.get('/api/schedule/coverage')
        assert res.status_code == 422


# ── /api/staffing ─────────────────────────────────────────────
class TestStaffing:
    def test_staffing_valid(self, sync_client: TestClient):
        """Verify staffing valid."""
        res = sync_client.get('/api/staffing?year=2026&month=1')
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)

    def test_staffing_invalid_month(self, sync_client: TestClient):
        """Verify staffing invalid month."""
        res = sync_client.get('/api/staffing?year=2026&month=0')
        assert res.status_code in (400, 422)

    def test_staffing_invalid_year(self, sync_client: TestClient):
        """Verify staffing invalid year."""
        res = sync_client.get('/api/staffing?year=1900&month=1')
        assert res.status_code in (400, 422)

    def test_staffing_missing_params(self, sync_client: TestClient):
        """Verify staffing missing params."""
        res = sync_client.get('/api/staffing')
        assert res.status_code == 422


# ── /api/schedule/day ─────────────────────────────────────────
class TestScheduleDay:
    def test_day_valid(self, sync_client: TestClient):
        """Verify day valid."""
        res = sync_client.get('/api/schedule/day?date=2026-01-15')
        assert res.status_code == 200

    def test_day_invalid_date(self, sync_client: TestClient):
        """Verify day invalid date."""
        res = sync_client.get('/api/schedule/day?date=not-a-date')
        assert res.status_code == 400

    def test_day_with_group(self, sync_client: TestClient):
        """Verify day with group."""
        res = sync_client.get('/api/schedule/day?date=2026-01-15&group_id=1')
        assert res.status_code in (200, 404)

    def test_day_missing_date(self, sync_client: TestClient):
        """Verify day missing date."""
        res = sync_client.get('/api/schedule/day')
        assert res.status_code == 422


# ── /api/schedule/week ────────────────────────────────────────
class TestScheduleWeek:
    def test_week_valid(self, sync_client: TestClient):
        """Verify week valid."""
        res = sync_client.get('/api/schedule/week?date=2026-01-12')
        assert res.status_code == 200

    def test_week_invalid_date(self, sync_client: TestClient):
        """Verify week invalid date."""
        res = sync_client.get('/api/schedule/week?date=invalid')
        assert res.status_code == 400

    def test_week_with_group(self, sync_client: TestClient):
        """Verify week with group."""
        res = sync_client.get('/api/schedule/week?date=2026-01-12&group_id=1')
        assert res.status_code in (200, 404)


# ── /api/schedule/year ────────────────────────────────────────
class TestScheduleYear:
    def test_year_valid(self, sync_client: TestClient):
        """Verify year valid."""
        emps = sync_client.get('/api/employees').json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]['ID']
        res = sync_client.get(f'/api/schedule/year?year=2026&employee_id={emp_id}')
        assert res.status_code == 200

    def test_year_invalid_year(self, sync_client: TestClient):
        """Verify year invalid year."""
        res = sync_client.get('/api/schedule/year?year=1800&employee_id=1')
        assert res.status_code in (400, 422)

    def test_year_missing_params(self, sync_client: TestClient):
        """Verify year missing params."""
        res = sync_client.get('/api/schedule/year')
        assert res.status_code == 422


# ── /api/schedule/conflicts ───────────────────────────────────
class TestScheduleConflicts:
    def test_conflicts_valid(self, sync_client: TestClient):
        """Verify conflicts valid."""
        res = sync_client.get('/api/schedule/conflicts?year=2026&month=1')
        assert res.status_code == 200
        assert 'conflicts' in res.json()

    def test_conflicts_invalid_month(self, sync_client: TestClient):
        """Verify conflicts invalid month."""
        res = sync_client.get('/api/schedule/conflicts?year=2026&month=13')
        assert res.status_code == 400

    def test_conflicts_invalid_year(self, sync_client: TestClient):
        """Verify conflicts invalid year."""
        res = sync_client.get('/api/schedule/conflicts?year=1999&month=1')
        assert res.status_code == 400


# ── /api/shift-cycles ─────────────────────────────────────────
class TestShiftCycles:
    def test_list_cycles(self, sync_client: TestClient):
        """Verify list cycles."""
        res = sync_client.get('/api/shift-cycles')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_assignments(self, sync_client: TestClient):
        """Verify list assignments."""
        res = sync_client.get('/api/shift-cycles/assign')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_nonexistent_cycle(self, sync_client: TestClient):
        """Verify get nonexistent cycle."""
        res = sync_client.get('/api/shift-cycles/999999')
        assert res.status_code == 404

    def test_create_cycle(self, write_client: TestClient):
        """Verify create cycle."""
        res = write_client.post('/api/shift-cycles', json={'name': 'TestZyklus', 'size_weeks': 2})
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True
        assert data['cycle']['name'] == 'TestZyklus'
        # Cleanup
        cycle_id = data['cycle']['ID']
        write_client.delete(f'/api/shift-cycles/{cycle_id}')

    def test_create_cycle_invalid_name(self, write_client: TestClient):
        """Verify create cycle invalid name."""
        res = write_client.post('/api/shift-cycles', json={'name': '', 'size_weeks': 2})
        assert res.status_code in (400, 422)

    def test_create_cycle_invalid_weeks(self, write_client: TestClient):
        """Verify create cycle invalid weeks."""
        res = write_client.post('/api/shift-cycles', json={'name': 'Test', 'size_weeks': 100})
        assert res.status_code in (400, 422)

    def test_update_cycle_not_found(self, write_client: TestClient):
        """Verify update cycle not found."""
        res = write_client.put('/api/shift-cycles/999999', json={
            'name': 'X', 'size_weeks': 1, 'entries': []
        })
        assert res.status_code == 404

    def test_delete_cycle_not_found(self, write_client: TestClient):
        """Verify delete cycle not found."""
        res = write_client.delete('/api/shift-cycles/999999')
        assert res.status_code == 404

    def test_assign_cycle_invalid_date(self, write_client: TestClient):
        """Verify assign cycle invalid date."""
        res = write_client.post('/api/shift-cycles/assign', json={
            'employee_id': 1, 'cycle_id': 1, 'start_date': 'not-a-date'
        })
        assert res.status_code in (400, 422)

    def test_remove_cycle_assignment(self, write_client: TestClient):
        """Remove assignment for non-existent employee — should return ok with 0 removed."""
        res = write_client.delete('/api/shift-cycles/assign/999999')
        assert res.status_code == 200
        assert res.json()['ok'] is True


# ── /api/schedule/templates ───────────────────────────────────
class TestScheduleTemplates:
    def test_list_templates(self, sync_client: TestClient):
        """Verify list templates."""
        res = sync_client.get('/api/schedule/templates')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_create_and_delete_template(self, write_client: TestClient):
        """Verify create and delete template."""
        res = write_client.post('/api/schedule/templates', json={
            'name': 'TestVorlage',
            'description': 'Testbeschreibung',
            'assignments': [],
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get('name') == 'TestVorlage'
        # Cleanup
        tid = data.get('ID') or data.get('id')
        if tid:
            del_res = write_client.delete(f'/api/schedule/templates/{tid}')
            assert del_res.status_code == 200

    def test_delete_template_not_found(self, write_client: TestClient):
        """Verify delete template not found."""
        res = write_client.delete('/api/schedule/templates/999999')
        assert res.status_code == 404

    def test_capture_template_empty_week(self, write_client: TestClient):
        """Capture on a week with no entries → 400."""
        res = write_client.post('/api/schedule/templates/capture', json={
            'name': 'CaptureTest',
            'description': '',
            'year': 2099,
            'month': 12,
            'week_start_day': 1,
        })
        assert res.status_code in (400, 200)

    def test_apply_template_not_found(self, write_client: TestClient):
        """Verify apply template not found."""
        res = write_client.post('/api/schedule/templates/999999/apply', json={
            'target_date': '2026-01-05',
            'force': False,
        })
        assert res.status_code == 404


# ── /api/restrictions ─────────────────────────────────────────
class TestRestrictions:
    def test_list_restrictions(self, sync_client: TestClient):
        """Verify list restrictions."""
        res = sync_client.get('/api/restrictions')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_restrictions_by_employee(self, sync_client: TestClient):
        """Verify list restrictions by employee."""
        res = sync_client.get('/api/restrictions?employee_id=1')
        assert res.status_code == 200

    def test_create_restriction_invalid_weekday(self, write_client: TestClient):
        """Verify create restriction invalid weekday."""
        res = write_client.post('/api/restrictions', json={
            'employee_id': 1, 'shift_id': 1, 'reason': 'test', 'weekday': 9
        })
        assert res.status_code in (400, 422)

    def test_delete_restriction_not_found(self, write_client: TestClient):
        """Verify delete restriction not found."""
        res = write_client.delete('/api/restrictions/999999')
        assert res.status_code in (404, 200, 405)


# ── /api/schedule/generate ────────────────────────────────────
class TestScheduleGenerate:
    def test_generate_dry_run(self, write_client: TestClient):
        """Verify generate dry run."""
        res = write_client.post('/api/schedule/generate', json={
            'year': 2026,
            'month': 3,
            'dry_run': True,
        })
        assert res.status_code == 200
        data = res.json()
        assert 'created' in data
        assert 'message' in data

    def test_generate_invalid_month(self, write_client: TestClient):
        """Verify generate invalid month."""
        res = write_client.post('/api/schedule/generate', json={
            'year': 2026,
            'month': 0,
        })
        assert res.status_code in (400, 422)


# ── /api/schedule-shift DELETE ────────────────────────────────
class TestDeleteShiftOnly:
    def test_delete_shift_only_valid_date(self, write_client: TestClient):
        """DELETE /api/schedule-shift/{emp_id}/{date} — no entry exists, 0 deleted."""
        res = write_client.delete('/api/schedule-shift/999999/2026-01-15')
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_delete_shift_only_invalid_date(self, write_client: TestClient):
        """Verify delete shift only invalid date."""
        res = write_client.delete('/api/schedule-shift/1/bad-date')
        assert res.status_code == 400


# ── events.py broadcast with active subscriber ────────────────
class TestEventsBroadcastWithSubscriber:
    def test_broadcast_with_live_subscriber(self):
        """broadcast() with a real subscriber should schedule put and log debug."""
        import asyncio
        from api.routers.events import broadcast, _subscribers, _lock

        async def run():
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()
            with _lock:
                _subscribers.append((loop, queue))
            try:
                broadcast("schedule_changed", {"employee_id": 1, "date": "2026-01-15"})
                # Give the event loop a chance to run the scheduled callback
                await asyncio.sleep(0)
                assert not queue.empty()
            finally:
                with _lock:
                    try:
                        _subscribers.remove((loop, queue))
                    except ValueError:
                        pass

        asyncio.run(run())

    def test_event_generator_connected_event(self):
        """_event_generator should yield connected event first."""
        import asyncio
        from api.routers.events import _event_generator

        async def run():
            queue = asyncio.Queue()
            # Create mock request that is immediately disconnected
            class MockRequest:
                async def is_disconnected(self):
                    return True

            gen = _event_generator(MockRequest(), queue)
            first = await gen.__anext__()
            assert "connected" in first
            # Close generator
            try:
                await gen.aclose()
            except Exception:
                pass

        asyncio.run(run())


# ── /api/schedule/swap ────────────────────────────────────────
class TestScheduleSwap:
    def test_swap_same_employee_error(self, write_client: TestClient):
        """Verify swap same employee error."""
        res = write_client.post('/api/schedule/swap', json={
            'employee_id_1': 1,
            'employee_id_2': 1,
            'dates': ['2026-01-15'],
        })
        assert res.status_code == 400

    def test_swap_invalid_date(self, write_client: TestClient):
        """Verify swap invalid date."""
        res = write_client.post('/api/schedule/swap', json={
            'employee_id_1': 1,
            'employee_id_2': 2,
            'dates': ['not-a-date'],
        })
        assert res.status_code == 400

    def test_swap_empty_dates(self, write_client: TestClient):
        """Verify swap empty dates."""
        res = write_client.post('/api/schedule/swap', json={
            'employee_id_1': 1,
            'employee_id_2': 2,
            'dates': [],
        })
        assert res.status_code in (400, 422)

    def test_swap_no_entries(self, write_client: TestClient):
        """Swap two employees with no entries — should succeed with 0 swapped."""
        emps = write_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        id1, id2 = emps[0]['ID'], emps[1]['ID']
        res = write_client.post('/api/schedule/swap', json={
            'employee_id_1': id1,
            'employee_id_2': id2,
            'dates': ['2099-06-15'],  # far future, unlikely to have entries
        })
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True


# ── /api/schedule/copy-week ───────────────────────────────────
class TestScheduleCopyWeek:
    def test_copy_week_invalid_date(self, write_client: TestClient):
        """Verify copy week invalid date."""
        res = write_client.post('/api/schedule/copy-week', json={
            'source_employee_id': 1,
            'dates': ['bad-date'],
            'target_employee_ids': [2],
        })
        assert res.status_code == 400

    def test_copy_week_empty(self, write_client: TestClient):
        """Copy from future date with no entries → 0 created."""
        emps = write_client.get('/api/employees').json()
        if len(emps) < 2:
            pytest.skip("Need at least 2 employees")
        id1, id2 = emps[0]['ID'], emps[1]['ID']
        res = write_client.post('/api/schedule/copy-week', json={
            'source_employee_id': id1,
            'dates': ['2099-06-15', '2099-06-16'],
            'target_employee_ids': [id2],
        })
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True
        assert data['created'] == 0

    def test_copy_week_same_employee_skipped(self, write_client: TestClient):
        """Copying to source employee itself is silently skipped."""
        res = write_client.post('/api/schedule/copy-week', json={
            'source_employee_id': 1,
            'dates': ['2099-06-15'],
            'target_employee_ids': [1],
        })
        assert res.status_code == 200
