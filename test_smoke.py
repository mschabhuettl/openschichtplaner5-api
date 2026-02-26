"""
Smoke tests for OpenSchichtplaner5 backend API endpoints.
These tests use FastAPI's TestClient and work without a real DBF database
by checking the API structure and response shapes.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the app
from api.main import app

client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def mock_db_factory(overrides: dict = None):
    """Build a mock SP5Database with sensible defaults."""
    db = MagicMock()
    defaults = {
        'get_stats': lambda: {
            'employees': 5, 'groups': 2, 'shifts': 3,
            'leave_types': 4, 'workplaces': 1, 'holidays': 10, 'users': 2,
        },
        'get_employees': lambda **kw: [],
        'get_groups': lambda **kw: [],
        'get_shifts': lambda **kw: [],
        'get_leave_types': lambda **kw: [],
        'get_workplaces': lambda **kw: [],
        'get_holidays': lambda **kw: [],
        'get_users': lambda: [],
        'get_schedule': lambda **kw: [],
        'get_schedule_day': lambda d, **kw: [],
        'get_schedule_week': lambda d, **kw: {
            'week_start': '2025-01-06',
            'week_end': '2025-01-12',
            'days': [],
        },
        'get_statistics': lambda yr, mo, **kw: [],
        'get_schedule_year': lambda yr, eid: [],
        'get_staffing': lambda yr, mo: [],
        'get_cycles': lambda: [],
        'get_group_members': lambda gid: [],
    }
    if overrides:
        defaults.update(overrides)
    for method, ret in defaults.items():
        if callable(ret):
            getattr(db, method).side_effect = ret
        else:
            getattr(db, method).return_value = ret
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRootEndpoint:
    def test_root_returns_service_info(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/')
        assert r.status_code == 200
        data = r.json()
        assert data['service'] == 'OpenSchichtplaner5 API'
        assert 'version' in data


class TestStatsEndpoint:
    def test_stats_shape(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/stats')
        assert r.status_code == 200
        data = r.json()
        for key in ('employees', 'groups', 'shifts', 'leave_types', 'workplaces', 'holidays', 'users'):
            assert key in data, f"Missing key: {key}"


class TestScheduleDayEndpoint:
    def test_valid_date_returns_list(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/day?date=2025-06-15')
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_invalid_date_returns_400(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/day?date=not-a-date')
        assert r.status_code == 400

    def test_missing_date_returns_422(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/day')
        assert r.status_code == 422


class TestScheduleWeekEndpoint:
    def test_valid_date_returns_week_structure(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/week?date=2025-06-15')
        assert r.status_code == 200
        data = r.json()
        assert 'week_start' in data
        assert 'week_end' in data
        assert 'days' in data

    def test_invalid_date_returns_400(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/week?date=bad-input')
        assert r.status_code == 400

    def test_group_id_param_accepted(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule/week?date=2025-06-15&group_id=1')
        assert r.status_code == 200


class TestStatisticsEndpoint:
    def test_requires_year_and_month(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/statistics?year=2025&month=6')
        assert r.status_code == 200

    def test_invalid_month_returns_400(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/statistics?year=2025&month=13')
        assert r.status_code == 400

    def test_returns_list(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/statistics?year=2025&month=1')
        assert isinstance(r.json(), list)


class TestScheduleMonthEndpoint:
    def test_valid_params(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule?year=2025&month=6')
        assert r.status_code == 200

    def test_invalid_month(self):
        with patch('api.main.get_db', return_value=mock_db_factory()):
            r = client.get('/api/schedule?year=2025&month=0')
        assert r.status_code == 400


class TestScheduleWeekLogic:
    """Test get_schedule_week directly on SP5Database (with mocked DBF reads)."""

    def test_week_contains_7_days(self):
        from sp5lib.database import SP5Database
        db = SP5Database('/nonexistent')
        # Patch all _read calls to return empty lists
        with patch.object(db, '_read', return_value=[]), \
             patch.object(db, 'get_employees', return_value=[]), \
             patch.object(db, 'get_group_members', return_value=[]), \
             patch.object(db, 'get_shifts', return_value=[]), \
             patch.object(db, 'get_leave_types', return_value=[]), \
             patch.object(db, 'get_workplaces', return_value=[]):
            result = db.get_schedule_week('2025-06-18')  # A Wednesday

        assert result['week_start'] == '2025-06-16'  # Monday
        assert result['week_end'] == '2025-06-22'    # Sunday
        assert len(result['days']) == 7

    def test_week_start_is_monday(self):
        from sp5lib.database import SP5Database
        db = SP5Database('/nonexistent')
        with patch.object(db, '_read', return_value=[]), \
             patch.object(db, 'get_employees', return_value=[]), \
             patch.object(db, 'get_group_members', return_value=[]), \
             patch.object(db, 'get_shifts', return_value=[]), \
             patch.object(db, 'get_leave_types', return_value=[]), \
             patch.object(db, 'get_workplaces', return_value=[]):
            # Test with a Monday input
            result = db.get_schedule_week('2025-06-16')

        assert result['week_start'] == '2025-06-16'
        assert result['week_end'] == '2025-06-22'

        # Test with a Sunday input
        with patch.object(db, '_read', return_value=[]), \
             patch.object(db, 'get_employees', return_value=[]), \
             patch.object(db, 'get_group_members', return_value=[]), \
             patch.object(db, 'get_shifts', return_value=[]), \
             patch.object(db, 'get_leave_types', return_value=[]), \
             patch.object(db, 'get_workplaces', return_value=[]):
            result2 = db.get_schedule_week('2025-06-22')

        assert result2['week_start'] == '2025-06-16'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
