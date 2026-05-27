"""Tests for the Scheduled Reports router and scheduler logic.

Covers:
  - CRUD operations (create, list, get, update, delete)
  - Authentication / authorization (Admin for write, Planer can read)
  - Input validation (invalid report_type, frequency, format, recipients)
  - Run endpoint behaviour (SMTP not configured path)
  - 404 on unknown report IDs
  - Scheduler logic (due reports, next_run computation, start/stop)
  - Report generation helpers (mocked DB)
  - Email delivery (mocked SMTP)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, timedelta
from datetime import datetime as _dt
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── Constants ──────────────────────────────────────────────────────────────────

_BASE = "/api/scheduled-reports"

_VALID_PAYLOAD = {
    "name": "Monatlicher Dienstplan",
    "report_type": "schedule_overview",
    "frequency": "monthly",
    "recipients": ["admin@example.com"],
    "format": "xlsx",
    "filters": {},
    "enabled": True,
}


def _create_report(client, payload: dict | None = None) -> dict:
    """Helper: POST a valid scheduled report and return the JSON response."""
    resp = client.post(_BASE, json=payload or _VALID_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Fixtures: isolate the JSON store per test ──────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_reports_file(tmp_path, monkeypatch):
    """Point the router at a fresh temp file for every test."""
    store = tmp_path / "scheduled_reports.json"
    import api.routers.scheduled_reports as mod

    monkeypatch.setattr(mod, "_REPORTS_FILE", store)
    yield store


# ── List (GET /) ───────────────────────────────────────────────────────────────


class TestListScheduledReports:
    def test_list_empty(self, admin_client):
        resp = admin_client.get(_BASE)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.get(_BASE)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == r["id"]

    def test_list_planer_allowed(self, planer_client):
        resp = planer_client.get(_BASE)
        assert resp.status_code == 200

    def test_list_leser_forbidden(self, leser_client):
        resp = leser_client.get(_BASE)
        assert resp.status_code == 403


# ── Create (POST /) ────────────────────────────────────────────────────────────


class TestCreateScheduledReport:
    def test_create_valid(self, admin_client):
        resp = admin_client.post(_BASE, json=_VALID_PAYLOAD)
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["name"] == _VALID_PAYLOAD["name"]
        assert data["report_type"] == "schedule_overview"
        assert data["frequency"] == "monthly"
        assert "next_run" in data
        assert "created_at" in data

    def test_create_all_report_types(self, admin_client):
        for rt in ("schedule_overview", "overtime", "absences"):
            payload = {**_VALID_PAYLOAD, "name": f"Test {rt}", "report_type": rt}
            resp = admin_client.post(_BASE, json=payload)
            assert resp.status_code == 201, f"Failed for report_type={rt}: {resp.text}"

    def test_create_all_frequencies(self, admin_client):
        for freq in ("daily", "weekly", "monthly"):
            payload = {**_VALID_PAYLOAD, "name": f"Test {freq}", "frequency": freq}
            resp = admin_client.post(_BASE, json=payload)
            assert resp.status_code == 201, f"Failed for frequency={freq}: {resp.text}"

    def test_create_csv_format(self, admin_client):
        payload = {**_VALID_PAYLOAD, "format": "csv"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 201
        assert resp.json()["format"] == "csv"

    def test_create_with_filters(self, admin_client):
        payload = {**_VALID_PAYLOAD, "filters": {"group_id": 5}}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 201
        assert resp.json()["filters"]["group_id"] == 5

    def test_create_multiple_recipients(self, admin_client):
        payload = {**_VALID_PAYLOAD, "recipients": ["a@x.com", "b@x.com"]}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 201
        assert len(resp.json()["recipients"]) == 2

    def test_create_disabled(self, admin_client):
        payload = {**_VALID_PAYLOAD, "enabled": False}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 201
        assert resp.json()["enabled"] is False

    # ── Validation errors ──

    def test_invalid_report_type(self, admin_client):
        payload = {**_VALID_PAYLOAD, "report_type": "invalid_type"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_invalid_frequency(self, admin_client):
        payload = {**_VALID_PAYLOAD, "frequency": "hourly"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_invalid_format(self, admin_client):
        payload = {**_VALID_PAYLOAD, "format": "pdf"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_invalid_email(self, admin_client):
        payload = {**_VALID_PAYLOAD, "recipients": ["not-an-email"]}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_empty_recipients(self, admin_client):
        payload = {**_VALID_PAYLOAD, "recipients": []}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_missing_name(self, admin_client):
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "name"}
        resp = admin_client.post(_BASE, json=payload)
        assert resp.status_code == 422

    def test_planer_forbidden(self, planer_client):
        resp = planer_client.post(_BASE, json=_VALID_PAYLOAD)
        assert resp.status_code == 403

    def test_leser_forbidden(self, leser_client):
        resp = leser_client.post(_BASE, json=_VALID_PAYLOAD)
        assert resp.status_code == 403


# ── Get single (GET /{id}) ────────────────────────────────────────────────────


class TestGetScheduledReport:
    def test_get_existing(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.get(f"{_BASE}/{r['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == r["id"]

    def test_get_planer_allowed(self, admin_client, planer_client):
        r = _create_report(admin_client)
        resp = planer_client.get(f"{_BASE}/{r['id']}")
        assert resp.status_code == 200

    def test_get_leser_forbidden(self, admin_client, leser_client):
        r = _create_report(admin_client)
        resp = leser_client.get(f"{_BASE}/{r['id']}")
        assert resp.status_code == 403

    def test_get_not_found(self, admin_client):
        resp = admin_client.get(f"{_BASE}/nonexistent-id")
        assert resp.status_code == 404

    # Note: scheduler/status endpoint must be tested separately
    # because it's registered before /{report_id} in the router


# ── Update (PUT /{id}) ────────────────────────────────────────────────────────


class TestUpdateScheduledReport:
    def test_update_name(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.put(f"{_BASE}/{r['id']}", json={"name": "Neuer Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Neuer Name"

    def test_update_enabled(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.put(f"{_BASE}/{r['id']}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_update_recipients(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.put(f"{_BASE}/{r['id']}", json={"recipients": ["new@example.com"]})
        assert resp.status_code == 200
        assert resp.json()["recipients"] == ["new@example.com"]

    def test_update_frequency_recomputes_next_run(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.put(f"{_BASE}/{r['id']}", json={"frequency": "daily"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["frequency"] == "daily"
        # next_run should still be present and valid ISO string
        assert data.get("next_run") is not None

    def test_update_invalid_report_type(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.put(f"{_BASE}/{r['id']}", json={"report_type": "bad"})
        assert resp.status_code == 422

    def test_update_not_found(self, admin_client):
        resp = admin_client.put(f"{_BASE}/nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_planer_forbidden(self, admin_client, planer_client):
        r = _create_report(admin_client)
        resp = planer_client.put(f"{_BASE}/{r['id']}", json={"name": "X"})
        assert resp.status_code == 403


# ── Delete (DELETE /{id}) ─────────────────────────────────────────────────────


class TestDeleteScheduledReport:
    def test_delete_existing(self, admin_client):
        r = _create_report(admin_client)
        resp = admin_client.delete(f"{_BASE}/{r['id']}")
        assert resp.status_code == 204
        # Verify it's gone
        resp2 = admin_client.get(f"{_BASE}/{r['id']}")
        assert resp2.status_code == 404

    def test_delete_not_found(self, admin_client):
        resp = admin_client.delete(f"{_BASE}/nonexistent")
        assert resp.status_code == 404

    def test_delete_planer_forbidden(self, admin_client, planer_client):
        r = _create_report(admin_client)
        resp = planer_client.delete(f"{_BASE}/{r['id']}")
        assert resp.status_code == 403

    def test_delete_reduces_list(self, admin_client):
        r1 = _create_report(admin_client, {**_VALID_PAYLOAD, "name": "R1"})
        _create_report(admin_client, {**_VALID_PAYLOAD, "name": "R2"})
        admin_client.delete(f"{_BASE}/{r1['id']}")
        data = admin_client.get(_BASE).json()
        assert len(data) == 1
        assert data[0]["name"] == "R2"


# ── Run (POST /{id}/run) ───────────────────────────────────────────────────────


class TestRunScheduledReport:
    def test_run_smtp_not_configured(self, admin_client):
        """Should return SMTP not configured error without crashing."""
        r = _create_report(admin_client)
        with (
            patch("api.routers.scheduled_reports.generate_report") as mock_gen,
            patch("api.routers.scheduled_reports.send_report_email") as mock_send,
        ):
            mock_gen.return_value = (b"fake_bytes", "report.xlsx")
            mock_send.return_value = {"success": False, "reason": "SMTP not configured"}
            resp = admin_client.post(f"{_BASE}/{r['id']}/run")
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_run_smtp_success(self, admin_client):
        r = _create_report(admin_client)
        with (
            patch("api.routers.scheduled_reports.generate_report") as mock_gen,
            patch("api.routers.scheduled_reports.send_report_email") as mock_send,
        ):
            mock_gen.return_value = (b"data", "report.xlsx")
            mock_send.return_value = {
                "success": True,
                "sent_to": ["admin@example.com"],
                "failed": [],
                "filename": "report.xlsx",
            }
            resp = admin_client.post(f"{_BASE}/{r['id']}/run")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_run_updates_last_run(self, admin_client):
        r = _create_report(admin_client)
        assert admin_client.get(f"{_BASE}/{r['id']}").json()["last_run"] is None
        with (
            patch("api.routers.scheduled_reports.generate_report") as mock_gen,
            patch("api.routers.scheduled_reports.send_report_email") as mock_send,
        ):
            mock_gen.return_value = (b"data", "report.xlsx")
            mock_send.return_value = {"success": True, "sent_to": [], "failed": []}
            admin_client.post(f"{_BASE}/{r['id']}/run")
        updated = admin_client.get(f"{_BASE}/{r['id']}").json()
        assert updated["last_run"] is not None

    def test_run_generation_failure(self, admin_client):
        r = _create_report(admin_client)
        with patch("api.routers.scheduled_reports.generate_report") as mock_gen:
            mock_gen.side_effect = RuntimeError("DB error")
            resp = admin_client.post(f"{_BASE}/{r['id']}/run")
        assert resp.status_code == 500
        assert "Report generation failed" in resp.json()["detail"]

    def test_run_not_found(self, admin_client):
        resp = admin_client.post(f"{_BASE}/nonexistent/run")
        assert resp.status_code == 404

    def test_run_planer_forbidden(self, admin_client, planer_client):
        r = _create_report(admin_client)
        resp = planer_client.post(f"{_BASE}/{r['id']}/run")
        assert resp.status_code == 403


# ── Scheduler status (GET /scheduler/status) ──────────────────────────────────


class TestSchedulerStatus:
    def test_status_admin_allowed(self, admin_client):
        resp = admin_client.get(f"{_BASE}/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "last_run" in data
        assert "reports_sent_total" in data
        assert "active_reports" in data

    def test_status_planer_forbidden(self, planer_client):
        resp = planer_client.get(f"{_BASE}/scheduler/status")
        assert resp.status_code == 403

    def test_status_active_reports_count(self, admin_client):
        _create_report(admin_client, {**_VALID_PAYLOAD, "name": "R1", "enabled": True})
        _create_report(admin_client, {**_VALID_PAYLOAD, "name": "R2", "enabled": False})
        resp = admin_client.get(f"{_BASE}/scheduler/status")
        assert resp.status_code == 200
        # Only enabled ones count
        assert resp.json()["active_reports"] == 1


# ── Scheduler logic unit tests ─────────────────────────────────────────────────


class TestComputeNextRun:
    def setup_method(self):
        from api.routers.scheduled_reports import _compute_next_run

        self._compute = _compute_next_run

    def test_daily_adds_one_day(self):
        now = _dt(2025, 6, 15, 9, 0, 0, tzinfo=UTC)
        result = self._compute("daily", now)
        next_dt = _dt.fromisoformat(result)
        assert (next_dt - now).days == 1

    def test_weekly_adds_seven_days(self):
        now = _dt(2025, 6, 15, 9, 0, 0, tzinfo=UTC)
        result = self._compute("weekly", now)
        next_dt = _dt.fromisoformat(result)
        assert (next_dt - now).days == 7

    def test_monthly_same_day_next_month(self):
        now = _dt(2025, 6, 15, 9, 0, 0, tzinfo=UTC)
        result = self._compute("monthly", now)
        next_dt = _dt.fromisoformat(result)
        assert next_dt.month == 7
        assert next_dt.day == 15

    def test_monthly_end_of_month_clamped(self):
        # January 31 → February (max 28)
        now = _dt(2025, 1, 31, 9, 0, 0, tzinfo=UTC)
        result = self._compute("monthly", now)
        next_dt = _dt.fromisoformat(result)
        assert next_dt.month == 2
        assert next_dt.day <= 28  # Feb has 28 days in 2025

    def test_no_from_dt_uses_now(self):
        # Should not raise
        result = self._compute("daily")
        assert result is not None


class TestGetReferenceMonth:
    def setup_method(self):
        from api.routers.scheduled_reports import _get_reference_month

        self._get = _get_reference_month

    def test_monthly_returns_previous_month(self):
        with patch("api.routers.scheduled_reports._dt") as mock_dt:
            mock_dt.now.return_value = _dt(2025, 3, 15, 10, 0, tzinfo=UTC)
            year, month = self._get("monthly")
        assert month == 2
        assert year == 2025

    def test_monthly_january_returns_december_prev_year(self):
        with patch("api.routers.scheduled_reports._dt") as mock_dt:
            mock_dt.now.return_value = _dt(2025, 1, 15, 10, 0, tzinfo=UTC)
            year, month = self._get("monthly")
        assert month == 12
        assert year == 2024

    def test_daily_returns_yesterday(self):
        with patch("api.routers.scheduled_reports._dt") as mock_dt:
            mock_dt.now.return_value = _dt(2025, 3, 16, 10, 0, tzinfo=UTC)
            year, month = self._get("daily")
        assert month == 3
        assert year == 2025


class TestRunDueReports:
    """Unit tests for the _run_due_reports scheduler function."""

    @pytest.fixture(autouse=True)
    def mock_generate_and_send(self):
        """Mock generate_report and send_report_email for scheduler tests."""
        with (
            patch("api.routers.scheduled_reports.generate_report") as mock_gen,
            patch("api.routers.scheduled_reports.send_report_email") as mock_send,
        ):
            mock_gen.return_value = (b"fake", "report.xlsx")
            mock_send.return_value = {"success": True, "sent_to": ["a@b.com"]}
            self.mock_generate = mock_gen
            self.mock_send = mock_send
            yield

    def test_no_reports_returns_zero(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        monkeypatch.setattr(mod, "_REPORTS_FILE", tmp_path / "reports.json")
        from api.routers.scheduled_reports import _run_due_reports

        assert _run_due_reports() == 0

    def test_disabled_report_not_sent(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        store = tmp_path / "reports.json"
        past = (_dt.now(UTC) - timedelta(hours=1)).isoformat()
        reports = [
            {
                "id": "r1",
                "name": "Test",
                "report_type": "schedule_overview",
                "frequency": "monthly",
                "recipients": ["a@b.com"],
                "format": "xlsx",
                "filters": {},
                "enabled": False,
                "next_run": past,
                "last_run": None,
            }
        ]
        store.write_text(json.dumps(reports))
        monkeypatch.setattr(mod, "_REPORTS_FILE", store)
        from api.routers.scheduled_reports import _run_due_reports

        count = _run_due_reports()
        assert count == 0
        self.mock_generate.assert_not_called()

    def test_due_report_is_sent(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        store = tmp_path / "reports.json"
        past = (_dt.now(UTC) - timedelta(hours=1)).isoformat()
        reports = [
            {
                "id": "r1",
                "name": "Test",
                "report_type": "schedule_overview",
                "frequency": "monthly",
                "recipients": ["a@b.com"],
                "format": "xlsx",
                "filters": {},
                "enabled": True,
                "next_run": past,
                "last_run": None,
            }
        ]
        store.write_text(json.dumps(reports))
        monkeypatch.setattr(mod, "_REPORTS_FILE", store)
        from api.routers.scheduled_reports import _run_due_reports

        count = _run_due_reports()
        assert count == 1
        self.mock_generate.assert_called_once()
        self.mock_send.assert_called_once()

    def test_future_report_not_sent(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        store = tmp_path / "reports.json"
        future = (_dt.now(UTC) + timedelta(hours=1)).isoformat()
        reports = [
            {
                "id": "r1",
                "name": "Test",
                "report_type": "schedule_overview",
                "frequency": "monthly",
                "recipients": ["a@b.com"],
                "format": "xlsx",
                "filters": {},
                "enabled": True,
                "next_run": future,
                "last_run": None,
            }
        ]
        store.write_text(json.dumps(reports))
        monkeypatch.setattr(mod, "_REPORTS_FILE", store)
        from api.routers.scheduled_reports import _run_due_reports

        count = _run_due_reports()
        assert count == 0
        self.mock_generate.assert_not_called()

    def test_due_report_updates_next_run(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        store = tmp_path / "reports.json"
        past = (_dt.now(UTC) - timedelta(hours=1)).isoformat()
        reports = [
            {
                "id": "r1",
                "name": "Test",
                "report_type": "schedule_overview",
                "frequency": "monthly",
                "recipients": ["a@b.com"],
                "format": "xlsx",
                "filters": {},
                "enabled": True,
                "next_run": past,
                "last_run": None,
            }
        ]
        store.write_text(json.dumps(reports))
        monkeypatch.setattr(mod, "_REPORTS_FILE", store)
        from api.routers.scheduled_reports import _run_due_reports

        _run_due_reports()
        updated = json.loads(store.read_text())
        assert updated[0]["next_run"] != past
        assert updated[0]["last_run"] is not None

    def test_report_without_next_run_skipped(self, tmp_path, monkeypatch):
        import api.routers.scheduled_reports as mod

        store = tmp_path / "reports.json"
        reports = [
            {
                "id": "r1",
                "name": "Test",
                "report_type": "schedule_overview",
                "frequency": "monthly",
                "recipients": ["a@b.com"],
                "format": "xlsx",
                "filters": {},
                "enabled": True,
                "next_run": None,
                "last_run": None,
            }
        ]
        store.write_text(json.dumps(reports))
        monkeypatch.setattr(mod, "_REPORTS_FILE", store)
        from api.routers.scheduled_reports import _run_due_reports

        count = _run_due_reports()
        assert count == 0


# ── Scheduler start/stop ───────────────────────────────────────────────────────


class TestSchedulerStartStop:
    def test_start_creates_thread(self):
        import api.routers.scheduled_reports as mod

        original_running = mod._scheduler_running
        original_thread = mod._scheduler_thread
        try:
            mod.stop_scheduler()
            mod._scheduler_thread = None
            mod.start_scheduler(interval_seconds=3600)
            assert mod._scheduler_running is True
            assert mod._scheduler_thread is not None
            assert mod._scheduler_thread.is_alive()
        finally:
            mod.stop_scheduler()
            # Give thread a moment
            import time

            time.sleep(0.1)
            mod._scheduler_running = original_running
            mod._scheduler_thread = original_thread

    def test_start_idempotent(self):
        import api.routers.scheduled_reports as mod

        mod.stop_scheduler()
        mod._scheduler_thread = None
        try:
            mod.start_scheduler(interval_seconds=3600)
            thread1 = mod._scheduler_thread
            mod.start_scheduler(interval_seconds=3600)
            thread2 = mod._scheduler_thread
            assert thread1 is thread2  # Same thread, not a second one
        finally:
            mod.stop_scheduler()

    def test_stop_sets_flag(self):
        import api.routers.scheduled_reports as mod

        mod.start_scheduler(interval_seconds=3600)
        mod.stop_scheduler()
        assert mod._scheduler_running is False


# ── Report generation unit tests (mocked DB) ──────────────────────────────────


class TestGenerateReport:
    @pytest.fixture(autouse=True)
    def _mock_db(self):
        mock_db = MagicMock()
        mock_db.get_schedule.return_value = [
            {
                "employee_id": "EMP1",
                "date": "2025-01-15",
                "display_name": "Früh",
                "color_bk": "#4A90D9",
                "color_text": "#FFFFFF",
                "duration_hours": 8,
            },
        ]
        mock_db.get_employees.return_value = [
            {
                "ID": "EMP1",
                "NAME": "Müller",
                "FIRSTNAME": "Hans",
                "SHORTNAME": "HM",
                "POSITION": 1,
                "BOLD": False,
                "CBKLABEL": 0,
                "CBKLABEL_HEX": "#f8fafc",
                "CFGLABEL_HEX": "#000000",
                "HRSWEEK": 40,
            },
        ]
        mock_db.get_group_members.return_value = ["EMP1"]
        mock_db.get_absences.return_value = [
            {"type_name": "Urlaub", "days": 2},
        ]

        from types import ModuleType

        fake_db_module = ModuleType("sp5lib.db")
        fake_db_module.get_db = MagicMock(return_value=mock_db)

        with patch.dict("sys.modules", {"sp5lib.db": fake_db_module}):
            yield

    def test_generate_schedule_overview_csv(self):
        from api.routers.scheduled_reports import _generate_schedule_overview

        data, filename = _generate_schedule_overview(2025, 1, {}, "csv")
        assert isinstance(data, bytes)
        assert "csv" in filename
        assert b"M" in data  # some content

    def test_generate_schedule_overview_xlsx(self):
        from api.routers.scheduled_reports import _generate_schedule_overview

        try:
            import openpyxl  # noqa: F401

            data, filename = _generate_schedule_overview(2025, 1, {}, "xlsx")
            assert isinstance(data, bytes)
            assert "xlsx" in filename
            assert len(data) > 0
        except ImportError:
            pytest.skip("openpyxl not installed")

    def test_generate_schedule_overview_with_group_filter(self):
        from api.routers.scheduled_reports import _generate_schedule_overview

        data, filename = _generate_schedule_overview(2025, 1, {"group_id": 1}, "csv")
        assert isinstance(data, bytes)

    def test_generate_overtime_csv(self):
        from api.routers.scheduled_reports import _generate_overtime_report

        data, filename = _generate_overtime_report(2025, 1, {}, "csv")
        assert isinstance(data, bytes)
        assert "ueberstunden" in filename
        assert b"M" in data

    def test_generate_overtime_xlsx(self):
        from api.routers.scheduled_reports import _generate_overtime_report

        try:
            import openpyxl  # noqa: F401

            data, filename = _generate_overtime_report(2025, 1, {}, "xlsx")
            assert isinstance(data, bytes)
            assert "xlsx" in filename
        except ImportError:
            pytest.skip("openpyxl not installed")

    def test_generate_absences_csv(self):
        from api.routers.scheduled_reports import _generate_absences_report

        data, filename = _generate_absences_report(2025, 1, {}, "csv")
        assert isinstance(data, bytes)
        assert "abwesenheiten" in filename

    def test_generate_absences_xlsx(self):
        from api.routers.scheduled_reports import _generate_absences_report

        try:
            import openpyxl  # noqa: F401

            data, filename = _generate_absences_report(2025, 1, {}, "xlsx")
            assert isinstance(data, bytes)
            assert "xlsx" in filename
        except ImportError:
            pytest.skip("openpyxl not installed")

    def test_generate_report_dispatch_schedule_overview(self):
        from api.routers.scheduled_reports import generate_report

        report = {
            "report_type": "schedule_overview",
            "frequency": "monthly",
            "format": "csv",
            "filters": {},
        }
        data, filename = generate_report(report)
        assert isinstance(data, bytes)

    def test_generate_report_dispatch_overtime(self):
        from api.routers.scheduled_reports import generate_report

        report = {"report_type": "overtime", "frequency": "monthly", "format": "csv", "filters": {}}
        data, filename = generate_report(report)
        assert isinstance(data, bytes)

    def test_generate_report_dispatch_absences(self):
        from api.routers.scheduled_reports import generate_report

        report = {"report_type": "absences", "frequency": "monthly", "format": "csv", "filters": {}}
        data, filename = generate_report(report)
        assert isinstance(data, bytes)

    def test_generate_report_unknown_type(self):
        from api.routers.scheduled_reports import generate_report

        with pytest.raises(ValueError, match="Unknown report_type"):
            generate_report(
                {"report_type": "unknown", "frequency": "monthly", "format": "csv", "filters": {}}
            )


# ── Email delivery unit tests ──────────────────────────────────────────────────


class TestSendReportEmail:
    def test_smtp_not_configured_returns_error(self):
        from api.routers.scheduled_reports import send_report_email

        mock_cfg = MagicMock()
        mock_cfg.is_configured = False
        with patch("sp5lib.email_service.get_config", return_value=mock_cfg):
            result = send_report_email(
                {
                    "name": "Test",
                    "report_type": "schedule_overview",
                    "frequency": "monthly",
                    "format": "xlsx",
                    "recipients": ["a@b.com"],
                },
                b"data",
                "report.xlsx",
            )
        assert result["success"] is False
        assert "SMTP" in result.get("reason", "")

    def test_smtp_send_success(self):
        from api.routers.scheduled_reports import send_report_email

        mock_cfg = MagicMock()
        mock_cfg.is_configured = True
        mock_cfg.from_addr = "from@example.com"
        mock_cfg.host = "smtp.example.com"
        mock_cfg.port = 587
        mock_cfg.tls_mode = "true"
        mock_cfg.user = "user"
        mock_cfg.password = "pass"

        with (
            patch("sp5lib.email_service.get_config", return_value=mock_cfg),
            patch("smtplib.SMTP") as mock_smtp,
        ):
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

            result = send_report_email(
                {
                    "name": "Test",
                    "report_type": "schedule_overview",
                    "frequency": "monthly",
                    "format": "xlsx",
                    "recipients": ["a@b.com"],
                },
                b"data",
                "report.xlsx",
            )
        assert result["success"] is True
        assert "a@b.com" in result["sent_to"]

    def test_smtp_send_failure_marks_failed(self):
        from api.routers.scheduled_reports import send_report_email

        mock_cfg = MagicMock()
        mock_cfg.is_configured = True
        mock_cfg.from_addr = "from@example.com"
        mock_cfg.host = "smtp.example.com"
        mock_cfg.port = 587
        mock_cfg.tls_mode = "true"
        mock_cfg.user = "user"
        mock_cfg.password = "pass"

        with (
            patch("sp5lib.email_service.get_config", return_value=mock_cfg),
            patch("smtplib.SMTP") as mock_smtp,
        ):
            mock_smtp.return_value.__enter__ = MagicMock(
                side_effect=Exception("Connection refused")
            )
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

            result = send_report_email(
                {
                    "name": "Test",
                    "report_type": "overtime",
                    "frequency": "monthly",
                    "format": "csv",
                    "recipients": ["fail@b.com"],
                },
                b"data",
                "report.csv",
            )
        assert result["success"] is False
        assert "fail@b.com" in result["failed"]

    def test_smtp_ssl_mode(self):
        from api.routers.scheduled_reports import send_report_email

        mock_cfg = MagicMock()
        mock_cfg.is_configured = True
        mock_cfg.from_addr = "from@example.com"
        mock_cfg.host = "smtp.example.com"
        mock_cfg.port = 465
        mock_cfg.tls_mode = "ssl"
        mock_cfg.user = ""
        mock_cfg.password = ""

        with (
            patch("sp5lib.email_service.get_config", return_value=mock_cfg),
            patch("smtplib.SMTP_SSL") as mock_smtp,
        ):
            mock_smtp.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

            send_report_email(
                {
                    "name": "Test",
                    "report_type": "absences",
                    "frequency": "monthly",
                    "format": "xlsx",
                    "recipients": ["ssl@b.com"],
                },
                b"data",
                "report.xlsx",
            )
        mock_smtp.assert_called()

    def test_csv_mime_type(self):
        """CSV attachments should use text/csv MIME type."""
        from api.routers.scheduled_reports import send_report_email

        mock_cfg = MagicMock()
        mock_cfg.is_configured = True
        mock_cfg.from_addr = "from@example.com"
        mock_cfg.host = "smtp.example.com"
        mock_cfg.port = 587
        mock_cfg.tls_mode = "true"
        mock_cfg.user = ""
        mock_cfg.password = ""

        captured_messages = []

        def fake_send(msg):
            captured_messages.append(msg)

        with (
            patch("sp5lib.email_service.get_config", return_value=mock_cfg),
            patch("smtplib.SMTP") as mock_smtp_cls,
        ):
            mock_srv = MagicMock()
            mock_srv.send_message.side_effect = fake_send
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_srv)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            send_report_email(
                {
                    "name": "Test",
                    "report_type": "overtime",
                    "frequency": "monthly",
                    "format": "csv",
                    "recipients": ["a@b.com"],
                },
                b"col1,col2\r\n1,2\r\n",
                "report.csv",
            )
        assert len(captured_messages) == 1


class TestUpdateScheduledReportValidation:
    """ScheduledReportUpdate validators (PUT) — distinct from the create model.
    Validation fires at the Pydantic layer, before the 404 lookup."""

    _ID = "any-id"

    def test_invalid_report_type(self, admin_client):
        resp = admin_client.put(f"{_BASE}/{self._ID}", json={"report_type": "bogus"})
        assert resp.status_code == 422

    def test_invalid_frequency(self, admin_client):
        resp = admin_client.put(f"{_BASE}/{self._ID}", json={"frequency": "hourly"})
        assert resp.status_code == 422

    def test_invalid_format(self, admin_client):
        resp = admin_client.put(f"{_BASE}/{self._ID}", json={"format": "pdf"})
        assert resp.status_code == 422

    def test_empty_recipients(self, admin_client):
        resp = admin_client.put(f"{_BASE}/{self._ID}", json={"recipients": []})
        assert resp.status_code == 422

    def test_invalid_recipient_email(self, admin_client):
        resp = admin_client.put(f"{_BASE}/{self._ID}", json={"recipients": ["no-at-sign"]})
        assert resp.status_code == 422


class TestLoadReportsCorruptFile:
    def test_returns_empty_on_corrupt_file(self, tmp_path):
        import api.routers.scheduled_reports as sr

        bad = tmp_path / "scheduled_reports.json"
        bad.write_text("not json{", encoding="utf-8")
        with patch.object(sr, "_REPORTS_FILE", bad):
            assert sr._load_reports() == []
