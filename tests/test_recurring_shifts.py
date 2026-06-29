"""Tests for recurring shift pattern endpoints (Q066).

The list/create responses follow the frontend contract: a list returns a plain
array of enriched patterns ({id, employee_id, employee_name, shift_id,
shift_name, shift_short, recurrence, day_of_week, valid_from, valid_until}),
create returns that same enriched object, and generate returns {created, skipped}.
A pattern references a shift by ``shift_id`` only — the shift carries its own
start/end times, so the pattern does not duplicate them.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_recurring_file():
    """Remove stale recurring shift data before and after each test."""
    from sp5api.routers.recurring_shifts import _RECURRING_FILE

    if os.path.exists(_RECURRING_FILE):
        os.remove(_RECURRING_FILE)
    yield
    if os.path.exists(_RECURRING_FILE):
        os.remove(_RECURRING_FILE)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pattern_body(**kwargs):
    """Build a valid recurring shift pattern body with sensible defaults."""
    defaults = {
        "employee_id": 40,  # first employee in fixtures (group 51)
        "shift_id": 1,  # Frühschicht (ID=1 in fixtures)
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
        """Create a weekly recurring shift pattern → enriched object."""
        resp = write_client.post("/api/shifts/recurring", json=_pattern_body())
        assert resp.status_code == 200
        p = resp.json()
        assert p["recurrence"] == "weekly"
        assert p["day_of_week"] == 0
        assert isinstance(p["id"], int)
        assert p["employee_id"] == 40
        assert p["shift_id"] == 1
        # enrichment for direct rendering
        assert p["employee_name"]
        assert p["shift_short"] == "F"

    def test_create_biweekly(self, write_client):
        """Create a biweekly recurring shift pattern."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(recurrence="biweekly", day_of_week=2),
        )
        assert resp.status_code == 200
        assert resp.json()["recurrence"] == "biweekly"

    def test_create_no_valid_until(self, write_client):
        """valid_until can be null (indefinite pattern)."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_until=None),
        )
        assert resp.status_code == 200
        assert resp.json()["valid_until"] is None

    def test_create_missing_employee_id(self, write_client):
        """employee_id is required → validation error."""
        body = _pattern_body()
        del body["employee_id"]
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

    def test_create_nonexistent_shift(self, write_client):
        """An unknown shift id → 404."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(shift_id=999999),
        )
        assert resp.status_code == 404

    def test_create_requires_planer(self, leser_client):
        """Leser role cannot create patterns → 403."""
        resp = leser_client.post("/api/shifts/recurring", json=_pattern_body())
        assert resp.status_code == 403

    def test_ids_are_sequential_integers(self, write_client):
        """Each create allocates a distinct, increasing integer id."""
        first = write_client.post("/api/shifts/recurring", json=_pattern_body()).json()
        second = write_client.post(
            "/api/shifts/recurring", json=_pattern_body(day_of_week=1)
        ).json()
        assert isinstance(first["id"], int) and isinstance(second["id"], int)
        assert second["id"] > first["id"]


# ── GET /api/shifts/recurring ──────────────────────────────────────────────


class TestListRecurringShifts:
    def test_list_empty(self, write_client):
        """List returns an empty array when no patterns exist."""
        resp = write_client.get("/api/shifts/recurring")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_create(self, write_client):
        """List returns created patterns as a plain array of enriched objects."""
        write_client.post("/api/shifts/recurring", json=_pattern_body(day_of_week=1))
        write_client.post("/api/shifts/recurring", json=_pattern_body(day_of_week=3))
        resp = write_client.get("/api/shifts/recurring")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert all("employee_name" in p and "shift_name" in p for p in data)

    def test_list_filter_by_employee_id(self, write_client):
        """Filter by employee_id returns only matching patterns."""
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=40))
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=41))
        resp = write_client.get("/api/shifts/recurring?employee_id=40")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["employee_id"] == 40

    def test_list_filter_by_group_id(self, write_client):
        """Filter by group_id returns patterns of that group's members only.

        In fixtures: employee 40 ∈ group 51, employee 41 ∈ group 54.
        """
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=40))
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=41))
        resp = write_client.get("/api/shifts/recurring?group_id=51")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["employee_id"] == 40

    def test_list_filter_by_empty_group(self, write_client):
        """Filtering by a group with no members returns an empty array."""
        write_client.post("/api/shifts/recurring", json=_pattern_body(employee_id=40))
        resp = write_client.get("/api/shifts/recurring?group_id=999999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_no_auth_required(self, sync_client):
        """GET list works with a read-only session (no Planer required)."""
        resp = sync_client.get("/api/shifts/recurring")
        assert resp.status_code == 200


# ── DELETE /api/shifts/recurring/{id} ──────────────────────────────────────


class TestDeleteRecurringShift:
    def test_delete_existing(self, write_client):
        """Delete an existing pattern returns ok and removes it."""
        pattern_id = write_client.post(
            "/api/shifts/recurring", json=_pattern_body()
        ).json()["id"]

        del_resp = write_client.delete(f"/api/shifts/recurring/{pattern_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True
        assert del_resp.json()["deleted"] == pattern_id

        # Verify it's gone
        assert write_client.get("/api/shifts/recurring").json() == []

    def test_delete_nonexistent(self, write_client):
        """Delete a pattern that doesn't exist → 404."""
        resp = write_client.delete("/api/shifts/recurring/999999")
        assert resp.status_code == 404

    def test_delete_requires_planer(self, leser_client):
        """Leser role cannot delete patterns → 403."""
        resp = leser_client.delete("/api/shifts/recurring/1")
        assert resp.status_code == 403


# ── POST /api/shifts/recurring/{id}/generate ───────────────────────────────


class TestGenerateShifts:
    def test_generate_nonexistent_pattern(self, write_client):
        """Generate for unknown pattern → 404."""
        resp = write_client.post(
            "/api/shifts/recurring/999999/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 404

    def test_generate_invalid_date_range(self, write_client):
        """to_date before from_date → 422."""
        pattern_id = write_client.post(
            "/api/shifts/recurring", json=_pattern_body()
        ).json()["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-06-01", "to_date": "2026-01-01"},
        )
        assert resp.status_code == 422

    def test_generate_out_of_validity_range(self, write_client):
        """Generate entirely outside valid_from/valid_until → 0 created."""
        pattern_id = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_from="2026-01-01", valid_until="2026-03-31"),
        ).json()["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["skipped"] == 0

    def test_generate_response_structure(self, write_client):
        """Generate returns {created, skipped}."""
        pattern_id = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(
                employee_id=40,
                shift_id=1,
                recurrence="weekly",
                day_of_week=0,
                valid_from="2026-01-01",
                valid_until="2026-12-31",
            ),
        ).json()["id"]

        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "created" in data
        assert "skipped" in data
        assert data["created"] + data["skipped"] >= 0

    def test_generate_requires_planer(self, leser_client, write_client):
        """Leser cannot generate → 403."""
        pattern_id = write_client.post(
            "/api/shifts/recurring", json=_pattern_body()
        ).json()["id"]
        resp = leser_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 403


class TestRecurringShiftValidationAndErrors:
    """Validator inner branches and generation failure paths."""

    def test_create_calendar_invalid_date(self, write_client):
        """A date matching the pattern but not a real calendar date is rejected."""
        resp = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(valid_from="2026-13-01"),
        )
        assert resp.status_code == 422

    def test_generate_calendar_invalid_date(self, write_client):
        """A calendar-invalid date in the generate body → 422."""
        pattern_id = write_client.post(
            "/api/shifts/recurring", json=_pattern_body()
        ).json()["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-13-01", "to_date": "2026-12-31"},
        )
        assert resp.status_code == 422

    def test_generate_creates_new_entries(self, write_client):
        """Generating into a fresh date range actually creates schedule entries."""
        pattern_id = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(
                employee_id=40,
                shift_id=1,
                recurrence="weekly",
                day_of_week=0,
                valid_from="2027-01-01",
                valid_until="2027-12-31",
            ),
        ).json()["id"]
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2027-01-01", "to_date": "2027-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] >= 1

    def test_generate_proceeds_when_lookup_and_insert_fail(self, write_client, monkeypatch):
        """Generation tolerates a failed existing-shift lookup and counts failed
        inserts as skipped rather than erroring out."""
        pattern_id = write_client.post(
            "/api/shifts/recurring",
            json=_pattern_body(
                employee_id=40,
                day_of_week=0,
                recurrence="weekly",
                valid_from="2026-01-01",
                valid_until="2026-12-31",
            ),
        ).json()["id"]

        class _GenBoomDB:
            def _table(self, name):
                raise RuntimeError("MASHI lookup failed")  # best-effort path

            def add_schedule_entry(self, *a, **k):
                raise RuntimeError("insert failed")  # per-date failure → skipped

        monkeypatch.setattr("sp5api.routers.recurring_shifts.get_db", lambda: _GenBoomDB())
        resp = write_client.post(
            f"/api/shifts/recurring/{pattern_id}/generate",
            json={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["skipped"] >= 1
