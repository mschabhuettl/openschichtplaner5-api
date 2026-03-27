"""Tests for recurring shift pattern endpoints (Q066)."""

import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _clean_recurring_file():
    """Remove stale recurring shift data before and after each test."""
    from api.routers.recurring_shifts import _RECURRING_FILE

    if os.path.exists(_RECURRING_FILE):
        os.remove(_RECURRING_FILE)
    yield
    if os.path.exists(_RECURRING_FILE):
        os.remove(_RECURRING_FILE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pattern_body(**kwargs):
    """Build a valid recurring shift pattern body with sensible defaults."""
    defaults = {
        "employee_id": 40,   # first employee in fixtures
        "group_id": None,
        "shift_type": 1,     # Frühschicht (ID=1 in fixtures)
        "start_time": "06:00",
        "end_time": "14:00",
        "recurrence": "weekly",
        "day_of_week": 0,  # Monday
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
    }
    defaults.update(kwargs)
    return defaults


# ── POST /api/shifts/recurring ─────────────────────────────────────────────

class TestCreateRecurringShift:
    def test_create_basic_weekly(self, write_client):
        """Create a weekly recurring shift pattern."""
        resp = write_client.post("/api/shifts/recurring", json=_pattern_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        p = data["pattern"]
        assert p["recurrence"] == "weekly"
        assert p["day_of_week"] == 0
        assert "id" in p
        assert p["created_at"] is not None

    def test_create_biweekly(self, write_client):
        """Create a biweekly recurring shift pattern."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(recurrence="biweekly", day_of_week=2),
        )
        assert resp.status_code == 200
        assert resp.json()["pattern"]["recurrence"] == "biweekly"

    def test_create_no_valid_until(self, write_client):
        """valid_until can be null (indefinite pattern)."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_until=None),
        )
        assert resp.status_code == 200
        assert resp.json()["pattern"]["valid_until"] is None

    def test_create_missing_employee_and_group(self, write_client):
        """Both employee_id and group_id None → validation error."""
        body = _pattern_body(employee_id=None, group_id=None)
        resp = write_client.post("/api/shifts/recurring", json=body)
        assert resp.status_code == 422

    def test_create_invalid_day_of_week(self, write_client):
        """day_of_week outside 0-6 → 422."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(day_of_week=7),
        )
        assert resp.status_code == 422

    def test_create_invalid_recurrence(self, write_client):
        """Unknown recurrence value → 422."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(recurrence="daily"),
        )
        assert resp.status_code == 422

    def test_create_invalid_time_format(self, write_client):
        """Malformed time string → 422."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(start_time="6:00"),
        )
        assert resp.status_code == 422

    def test_create_invalid_date_format(self, write_client):
        """Malformed date string → 422."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_from="01-01-2026"),
        )
        assert resp.status_code == 422

    def test_create_valid_until_before_valid_from(self, write_client):
        """valid_until before valid_from → 422."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_from="2026-06-01", valid_until="2026-01-01"),
        )
        assert resp.status_code == 422

    def test_create_nonexistent_employee(self, write_client):
        """Employee 999999 does not exist → 404."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(employee_id=999999),
        )
        assert resp.status_code == 404

    def test_create_requires_planer(self, leser_client):
        """Leser role cannot create patterns → 403."""
        resp = leser_client.post("/api/shifts/recurring", json=_pattern_body())
        assert resp.status_code == 403


# ── GET /api/shifts/recurring ──────────────────────────────────────────────

class TestListRecurringShifts:
    def test_list_empty(self, write_client):
        """List returns empty when no patterns exist."""
        resp = write_client.get("/api/shifts/recurring")
        assert resp.status_code == 200
        data = resp.json()
        assert data["patterns"] == []
        assert data["total"] == 0

    def test_list_after_create(self, write_client):
        """List returns created patterns."""
        write_client.post("/api/shifts/recurring", json=_pattern_body(day_of_week=1))
        write_client.post("/api/shifts/recurring", json=_pattern_body(day_of_week=3))
        resp = write_client.get("/api/shifts/recurring")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_list_filter_by_employee_id(self, write_client):
        """Filter by employee_id returns only matching patterns."""
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=40))
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=41))
        resp = write_client.get("/api/shifts/recurring?employee_id=40")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["patterns"][0]["employee_id"] == 40

    def test_list_filter_by_group_id(self, write_client):
        """Filter by group_id returns only matching patterns."""
        write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(employee_id=None, group_id=10),
        )
        write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(employee_id=None, group_id=20),
        )
        resp = write_client.get("/api/shifts/recurring?group_id=10")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_no_auth_required(self, sync_client):
        """GET list works with a read-only session (no Planer required)."""
        resp = sync_client.get("/api/shifts/recurring")
        assert resp.status_code == 200


# ── DELETE /api/shifts/recurring/{id} ──────────────────────────────────────

class TestDeleteRecurringShift:
    def test_delete_existing(self, write_client):
        """Delete an existing pattern returns ok."""
        create_resp = write_client.post("/api/shifts/recurring", json=_pattern_body())
        pattern_id = create_resp.json()["pattern"]["id"]

        del_resp = write_client.delete(f"/api/shifts/recurring/{pattern_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # Verify it's gone
        list_resp = write_client.get("/api/shifts/recurring")
        assert list_resp.json()["total"] == 0

    def test_delete_nonexistent(self, write_client):
        """Delete a pattern that doesn't exist → 404."""
        fake_id = str(uuid.uuid4())
        resp = write_client.delete(f"/api/shifts/recurring/{fake_id}")
        assert resp.status_code == 404

    def test_delete_requires_planer(self, leser_client):
        """Leser role cannot delete patterns → 403."""
        fake_id = str(uuid.uuid4())
        resp = leser_client.delete(f"/api/shifts/recurring/{fake_id}")
        assert resp.status_code == 403


# ── POST /api/shifts/recurring/{id}/generate ───────────────────────────────

class TestGenerateShifts:
    def test_generate_nonexistent_pattern(self, write_client):
        """Generate for unknown pattern → 404."""
        fake_id = str(uuid.uuid4())
        resp = write_client.post(
            f"/api/shifts/recurring/{fake_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 404

    def test_generate_invalid_date_range(self, write_client):
        """to_date before from_date → 422."""
        create_resp = write_client.post("/api/shifts/recurring", json=_pattern_body())
        pattern_id = create_resp.json()["pattern"]["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-06-01", "to_date": "2026-01-01"},
        )
        assert resp.status_code == 422

    def test_generate_out_of_validity_range(self, write_client):
        """Generate entirely outside valid_from/valid_until → 0 generated."""
        create_resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_from="2026-01-01", valid_until="2026-03-31"),
        )
        pattern_id = create_resp.json()["pattern"]["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated"] == 0
        assert data["skipped"] == 0

    def test_generate_response_structure(self, write_client):
        """Generate returns {generated, skipped, dates}."""
        create_resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(
                employee_id=40,
                shift_type=1,
                recurrence="weekly",
                day_of_week=0,
                valid_from="2026-01-01",
                valid_until="2026-12-31",
            ),
        )
        assert create_resp.status_code == 200
        pattern_id = create_resp.json()["pattern"]["id"]

        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "generated" in data
        assert "skipped" in data
        assert "dates" in data
        assert isinstance(data["dates"], list)
        assert data["generated"] + data["skipped"] >= 0

    def test_generate_requires_planer(self, leser_client, write_client):
        """Leser cannot generate → 403."""
        create_resp = write_client.post("/api/shifts/recurring", json=_pattern_body())
        pattern_id = create_resp.json()["pattern"]["id"]
        resp = leser_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 403
