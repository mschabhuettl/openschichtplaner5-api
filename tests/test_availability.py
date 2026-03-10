"""Tests for employee availability endpoints (Q036)."""

import json
import os

import pytest


@pytest.fixture(autouse=True)
def _clean_availability_file():
    """Remove stale availability data before each test."""
    from api.routers.availability import _AVAILABILITY_FILE

    if os.path.exists(_AVAILABILITY_FILE):
        os.remove(_AVAILABILITY_FILE)
    yield
    # Cleanup after test too
    if os.path.exists(_AVAILABILITY_FILE):
        os.remove(_AVAILABILITY_FILE)


class TestAvailabilityEndpoints:
    """Test GET/POST/PUT /api/employees/{id}/availability."""

    def test_get_default_availability(self, sync_client):
        """GET returns default (all days available, no windows) when no data set."""
        resp = sync_client.get("/api/employees/40/availability")
        assert resp.status_code == 200
        data = resp.json()
        assert data["employee_id"] == 40
        assert len(data["days"]) == 7
        assert data["updated_at"] is None
        for d in data["days"]:
            assert d["available"] is True
            assert d["time_windows"] == []

    def test_get_availability_nonexistent_employee(self, sync_client):
        """GET returns 404 for non-existent employee."""
        resp = sync_client.get("/api/employees/99999/availability")
        assert resp.status_code == 404

    def test_set_availability_full_week(self, sync_client):
        """POST sets full weekly availability."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": True,
                    "time_windows": [{"start": "08:00", "end": "17:00"}],
                },
                {
                    "day": 1,
                    "available": True,
                    "time_windows": [{"start": "08:00", "end": "17:00"}],
                },
                {"day": 2, "available": True, "time_windows": []},
                {"day": 3, "available": True, "time_windows": []},
                {
                    "day": 4,
                    "available": True,
                    "time_windows": [
                        {"start": "06:00", "end": "12:00"},
                        {"start": "14:00", "end": "18:00"},
                    ],
                },
                {"day": 5, "available": False, "time_windows": []},
                {"day": 6, "available": False, "time_windows": []},
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        avail = data["availability"]
        assert avail["employee_id"] == 40
        assert len(avail["days"]) == 7
        assert avail["updated_at"] is not None

        # Verify GET returns the same data
        resp2 = sync_client.get("/api/employees/40/availability")
        assert resp2.status_code == 200
        assert resp2.json()["days"] == avail["days"]

    def test_set_availability_nonexistent_employee(self, sync_client):
        """POST returns 404 for non-existent employee."""
        body = {"days": [{"day": 0, "available": True, "time_windows": []}]}
        resp = sync_client.post("/api/employees/99999/availability", json=body)
        assert resp.status_code == 404

    def test_update_availability_partial(self, sync_client):
        """PUT updates only the specified days, leaving others unchanged."""
        # First set a baseline
        baseline = {
            "days": [
                {
                    "day": d,
                    "available": True,
                    "time_windows": [{"start": "09:00", "end": "17:00"}],
                }
                for d in range(7)
            ]
        }
        sync_client.post("/api/employees/40/availability", json=baseline)

        # Now update only Saturday (5) and Sunday (6)
        update = {
            "days": [
                {"day": 5, "available": False, "time_windows": []},
                {"day": 6, "available": False, "time_windows": []},
            ]
        }
        resp = sync_client.put("/api/employees/40/availability", json=update)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Verify: Mon-Fri should still have 09:00-17:00
        avail = data["availability"]
        days_map = {d["day"]: d for d in avail["days"]}
        for d in range(5):
            assert days_map[d]["available"] is True
            assert len(days_map[d]["time_windows"]) == 1
            assert days_map[d]["time_windows"][0]["start"] == "09:00"
        # Sat+Sun should be unavailable
        assert days_map[5]["available"] is False
        assert days_map[6]["available"] is False

    def test_update_availability_nonexistent_employee(self, sync_client):
        """PUT returns 404 for non-existent employee."""
        body = {"days": [{"day": 0, "available": True, "time_windows": []}]}
        resp = sync_client.put("/api/employees/99999/availability", json=body)
        assert resp.status_code == 404

    def test_validation_invalid_time(self, sync_client):
        """Reject invalid time format."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": True,
                    "time_windows": [{"start": "25:00", "end": "26:00"}],
                }
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_validation_end_before_start(self, sync_client):
        """Reject time window where end <= start."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": True,
                    "time_windows": [{"start": "17:00", "end": "08:00"}],
                }
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_validation_overlapping_windows(self, sync_client):
        """Reject overlapping time windows on the same day."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": True,
                    "time_windows": [
                        {"start": "08:00", "end": "14:00"},
                        {"start": "12:00", "end": "18:00"},
                    ],
                }
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_validation_windows_when_unavailable(self, sync_client):
        """Reject time windows when day is marked unavailable."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": False,
                    "time_windows": [{"start": "08:00", "end": "17:00"}],
                }
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_validation_duplicate_days(self, sync_client):
        """Reject duplicate day entries."""
        body = {
            "days": [
                {"day": 0, "available": True, "time_windows": []},
                {"day": 0, "available": False, "time_windows": []},
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_validation_day_out_of_range(self, sync_client):
        """Reject day values outside 0-6."""
        body = {"days": [{"day": 7, "available": True, "time_windows": []}]}
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422

    def test_multiple_time_windows_per_day(self, sync_client):
        """Accept multiple non-overlapping time windows."""
        body = {
            "days": [
                {
                    "day": 0,
                    "available": True,
                    "time_windows": [
                        {"start": "06:00", "end": "10:00"},
                        {"start": "12:00", "end": "16:00"},
                        {"start": "18:00", "end": "22:00"},
                    ],
                }
            ]
        }
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 200
        windows = resp.json()["availability"]["days"][0]["time_windows"]
        assert len(windows) == 3

    def test_update_creates_if_none_exists(self, sync_client):
        """PUT creates availability data if none exists for the employee."""
        # Use a different employee that might not have data yet
        # First verify we can GET default
        resp = sync_client.get("/api/employees/41/availability")
        if resp.status_code == 200:
            body = {"days": [{"day": 0, "available": False, "time_windows": []}]}
            resp = sync_client.put("/api/employees/41/availability", json=body)
            assert resp.status_code == 200
            avail = resp.json()["availability"]
            days_map = {d["day"]: d for d in avail["days"]}
            assert days_map[0]["available"] is False

    def test_empty_days_rejected(self, sync_client):
        """Reject empty days list."""
        body = {"days": []}
        resp = sync_client.post("/api/employees/40/availability", json=body)
        assert resp.status_code == 422
