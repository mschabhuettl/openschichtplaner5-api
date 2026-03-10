"""Tests for the in-memory TTL cache module (api/cache.py)."""

import time

import pytest
from api import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean cache."""
    cache.clear()
    yield
    cache.clear()


class TestCacheBasics:
    """Basic get/put/invalidate/clear operations."""

    def test_get_missing_returns_none(self):
        assert cache.get("nonexistent") is None

    def test_put_and_get(self):
        cache.put("key1", {"data": 42})
        assert cache.get("key1") == {"data": 42}

    def test_ttl_expiry(self):
        cache.put("short", "value", ttl=0.05)
        assert cache.get("short") == "value"
        time.sleep(0.06)
        assert cache.get("short") is None

    def test_invalidate_by_prefix(self):
        cache.put("employees:list:True", [1, 2])
        cache.put("employees:list:False", [3, 4])
        cache.put("shifts:list:False", [5])
        removed = cache.invalidate("employees:")
        assert removed == 2
        assert cache.get("employees:list:True") is None
        assert cache.get("employees:list:False") is None
        assert cache.get("shifts:list:False") == [5]

    def test_invalidate_multiple_prefixes(self):
        cache.put("employees:list:False", [1])
        cache.put("groups:list:False", [2])
        cache.put("shifts:list:False", [3])
        removed = cache.invalidate("employees:", "groups:")
        assert removed == 2
        assert cache.get("shifts:list:False") == [3]

    def test_clear(self):
        cache.put("a", 1)
        cache.put("b", 2)
        n = cache.clear()
        assert n == 2
        assert cache.get("a") is None

    def test_stats(self):
        cache.put("x", 1, ttl=60)
        cache.put("y", 2, ttl=0.001)
        time.sleep(0.01)
        s = cache.stats()
        assert s["total"] == 2
        assert s["active"] == 1
        assert s["expired"] == 1

    def test_get_or_set(self):
        calls = []

        def factory():
            calls.append(1)
            return [1, 2, 3]

        # First call invokes factory
        result = cache.get_or_set("test_key", factory)
        assert result == [1, 2, 3]
        assert len(calls) == 1

        # Second call returns cached
        result2 = cache.get_or_set("test_key", factory)
        assert result2 == [1, 2, 3]
        assert len(calls) == 1  # factory not called again

    def test_overwrite_existing_key(self):
        cache.put("k", "old")
        cache.put("k", "new")
        assert cache.get("k") == "new"


class TestCacheIntegrationWithEndpoints:
    """Test that cache is used and invalidated by API endpoints."""

    def test_employees_cached_on_second_call(self, write_client):
        """Two GET /api/employees calls should return same data; second from cache."""
        r1 = write_client.get("/api/employees")
        assert r1.status_code == 200

        # Cache should be populated now
        cached = cache.get("employees:list:False")
        assert cached is not None
        assert isinstance(cached, list)
        assert len(cached) == len(r1.json())

    def test_shifts_cached(self, write_client):
        r1 = write_client.get("/api/shifts")
        assert r1.status_code == 200
        assert cache.get("shifts:list:False") is not None

    def test_holidays_cached(self, write_client):
        r1 = write_client.get("/api/holidays")
        assert r1.status_code == 200
        assert cache.get("holidays:list:None") is not None

    def test_groups_cached(self, write_client):
        r1 = write_client.get("/api/groups")
        assert r1.status_code == 200
        assert cache.get("groups:list:False") is not None

    def test_leave_types_cached(self, write_client):
        r1 = write_client.get("/api/leave-types")
        assert r1.status_code == 200
        assert cache.get("leave_types:list:False") is not None

    def test_workplaces_cached(self, write_client):
        r1 = write_client.get("/api/workplaces")
        assert r1.status_code == 200
        assert cache.get("workplaces:list:False") is not None

    def test_employee_create_invalidates_cache(self, write_client):
        """Creating an employee should invalidate the employees cache."""
        # Populate cache
        write_client.get("/api/employees")
        assert cache.get("employees:list:False") is not None

        # Create employee
        write_client.post(
            "/api/employees",
            json={"NAME": "CacheTest"},
        )

        # Cache should be invalidated
        assert cache.get("employees:list:False") is None

    def test_shift_create_invalidates_cache(self, write_client):
        """Creating a shift should invalidate the shifts cache."""
        write_client.get("/api/shifts")
        assert cache.get("shifts:list:False") is not None

        write_client.post(
            "/api/shifts",
            json={"NAME": "CacheTestShift"},
        )
        assert cache.get("shifts:list:False") is None
