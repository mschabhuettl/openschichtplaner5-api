"""Tests for the Export Scheduler router.

Covers:
  - CRUD operations (create, list, update, delete)
  - Authentication / authorization (Admin only for write, Planer can read)
  - Input validation (invalid format, time, email, frequency)
  - Run endpoint behaviour (SMTP not configured path)
  - 404 on unknown schedule IDs
"""

from __future__ import annotations

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

_BASE = "/api/export-scheduler/schedules"

_VALID_PAYLOAD = {
    "name": "Wöchentlicher Report",
    "frequency": "weekly",
    "day_of_week": 0,
    "time": "08:00",
    "format": "xlsx",
    "group_id": None,
    "email_to": ["test@example.com"],
    "enabled": True,
}


def _create_schedule(client, payload: dict | None = None) -> dict:
    """Helper: POST a valid schedule and return the JSON response."""
    resp = client.post(_BASE, json=payload or _VALID_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Fixtures: isolate the JSON store per test ──────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_schedules_file(tmp_path, monkeypatch):
    """Point the router at a fresh temp file for every test."""
    store = tmp_path / "export_schedules.json"
    import api.routers.export_scheduler as mod

    monkeypatch.setattr(mod, "_SCHEDULES_FILE", store)
    yield store


# ── List (GET) ─────────────────────────────────────────────────────────────────


class TestListSchedules:
    def test_list_empty(self, admin_client):
        resp = admin_client.get(_BASE)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.get(_BASE)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == s["id"]

    def test_list_planer_allowed(self, planer_client):
        resp = planer_client.get(_BASE)
        assert resp.status_code == 200

    def test_list_leser_forbidden(self, leser_client):
        resp = leser_client.get(_BASE)
        assert resp.status_code == 403

    def test_list_unauthenticated(self, admin_client):
        from api.main import app
        from starlette.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get(_BASE)
        assert resp.status_code == 401


# ── Create (POST) ──────────────────────────────────────────────────────────────


class TestCreateSchedule:
    def test_create_success(self, admin_client):
        resp = admin_client.post(_BASE, json=_VALID_PAYLOAD)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == _VALID_PAYLOAD["name"]
        assert data["format"] == "xlsx"
        assert "id" in data
        assert "created_at" in data

    def test_create_csv_format(self, admin_client):
        payload = {**_VALID_PAYLOAD, "format": "csv"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 201
        assert resp.json()["format"] == "csv"

    def test_create_invalid_format(self, admin_client):
        payload = {**_VALID_PAYLOAD, "format": "pdf"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_invalid_time(self, admin_client):
        payload = {**_VALID_PAYLOAD, "time": "25:00"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_invalid_time_format(self, admin_client):
        payload = {**_VALID_PAYLOAD, "time": "8:00"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_invalid_frequency(self, admin_client):
        payload = {**_VALID_PAYLOAD, "frequency": "daily"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_invalid_email(self, admin_client):
        payload = {**_VALID_PAYLOAD, "email_to": ["not-an-email"]}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_empty_email_list(self, admin_client):
        payload = {**_VALID_PAYLOAD, "email_to": []}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_create_planer_forbidden(self, planer_client):
        resp = planer_client.post(_BASE, json=_VALID_PAYLOAD)
        assert resp.status_code == 403

    def test_create_multiple(self, admin_client):
        _create_schedule(admin_client, {**_VALID_PAYLOAD, "name": "A"})
        _create_schedule(admin_client, {**_VALID_PAYLOAD, "name": "B"})
        resp = admin_client.get(_BASE)
        assert len(resp.json()) == 2


# ── Update (PUT) ───────────────────────────────────────────────────────────────


class TestUpdateSchedule:
    def test_update_name(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.put(f"{_BASE}/{s['id']}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_update_enabled_false(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.put(f"{_BASE}/{s['id']}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_update_not_found(self, admin_client):
        resp = admin_client.put(f"{_BASE}/nonexistent-id", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_planer_forbidden(self, admin_client, planer_client):
        s = _create_schedule(admin_client)
        resp = planer_client.put(f"{_BASE}/{s['id']}", json={"name": "X"})
        assert resp.status_code == 403

    def test_update_invalid_format(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.put(f"{_BASE}/{s['id']}", json={"format": "docx"})
        assert resp.status_code == 422

    def test_update_preserves_other_fields(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.put(f"{_BASE}/{s['id']}", json={"name": "New name"})
        updated = resp.json()
        assert updated["format"] == _VALID_PAYLOAD["format"]
        assert updated["email_to"] == _VALID_PAYLOAD["email_to"]


# ── Delete (DELETE) ────────────────────────────────────────────────────────────


class TestDeleteSchedule:
    def test_delete_success(self, admin_client):
        s = _create_schedule(admin_client)
        resp = admin_client.delete(f"{_BASE}/{s['id']}")
        assert resp.status_code == 204
        # Verify gone
        resp2 = admin_client.get(_BASE)
        assert resp2.json() == []

    def test_delete_not_found(self, admin_client):
        resp = admin_client.delete(f"{_BASE}/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_planer_forbidden(self, admin_client, planer_client):
        s = _create_schedule(admin_client)
        resp = planer_client.delete(f"{_BASE}/{s['id']}")
        assert resp.status_code == 403


# ── Run (POST /{id}/run) ───────────────────────────────────────────────────────


class TestRunSchedule:
    def test_run_not_found(self, admin_client):
        resp = admin_client.post(f"{_BASE}/nonexistent-id/run")
        assert resp.status_code == 404

    def test_run_smtp_not_configured_returns_export_url(self, admin_client, monkeypatch):
        """When SMTP is not configured, run should return success=False + export_url."""
        s = _create_schedule(admin_client)

        # Patch get_config to return unconfigured
        import sp5lib.email_service as email_mod

        class _FakeConfig:
            is_configured = False
            host = ""
            port = 587
            user = ""
            password = ""
            from_addr = ""
            tls_mode = "true"
            app_url = "http://localhost"
            enabled = False

        monkeypatch.setattr(email_mod, "get_config", lambda: _FakeConfig())

        # Also patch the export generation to avoid DB dependency
        import api.routers.export_scheduler as sched_mod

        monkeypatch.setattr(sched_mod, "_generate_export", lambda fmt, gid, month: (b"fake", 5))

        resp = admin_client.post(f"{_BASE}/{s['id']}/run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "SMTP" in data["reason"]
        assert "export_url" in data

    def test_run_planer_forbidden(self, admin_client, planer_client):
        s = _create_schedule(admin_client)
        resp = planer_client.post(f"{_BASE}/{s['id']}/run")
        assert resp.status_code == 403

    def test_run_export_generation_error_returns_500(self, admin_client, monkeypatch):
        """If export generation raises, run should return 500."""
        s = _create_schedule(admin_client)

        import api.routers.export_scheduler as sched_mod

        def _failing_export(fmt, gid, month):
            raise RuntimeError("DB unavailable")

        monkeypatch.setattr(sched_mod, "_generate_export", _failing_export)

        resp = admin_client.post(f"{_BASE}/{s['id']}/run")
        assert resp.status_code == 500
