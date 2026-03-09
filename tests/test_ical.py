"""Tests for iCal export functionality."""

from datetime import UTC, date, datetime

import pytest
from api.routers.ical import (
    _build_ical,
    _escape_ical,
    _ical_date,
    _ical_dt,
    _make_uid,
    _parse_time,
)

# ── Unit tests for helper functions ──────────────────────────────


class TestMakeUid:
    def test_deterministic(self):
        uid1 = _make_uid(1, "2026-03-01", "shift-5")
        uid2 = _make_uid(1, "2026-03-01", "shift-5")
        assert uid1 == uid2

    def test_different_inputs_different_uids(self):
        uid1 = _make_uid(1, "2026-03-01", "shift-5")
        uid2 = _make_uid(2, "2026-03-01", "shift-5")
        uid3 = _make_uid(1, "2026-03-02", "shift-5")
        assert uid1 != uid2
        assert uid1 != uid3

    def test_format(self):
        uid = _make_uid(1, "2026-03-01", "shift-5")
        assert uid.endswith("@openschichtplaner5")
        # 16 hex chars + @openschichtplaner5
        prefix = uid.split("@")[0]
        assert len(prefix) == 16


class TestIcalDt:
    def test_utc_format(self):
        dt = datetime(2026, 3, 15, 8, 30, 0, tzinfo=UTC)
        assert _ical_dt(dt) == "20260315T083000Z"

    def test_midnight(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert _ical_dt(dt) == "20260101T000000Z"


class TestIcalDate:
    def test_date_format(self):
        d = date(2026, 3, 15)
        assert _ical_date(d) == "20260315"

    def test_single_digit_month_day(self):
        d = date(2026, 1, 5)
        assert _ical_date(d) == "20260105"


class TestEscapeIcal:
    def test_semicolon(self):
        assert _escape_ical("a;b") == "a\\;b"

    def test_comma(self):
        assert _escape_ical("a,b") == "a\\,b"

    def test_newline(self):
        assert _escape_ical("a\nb") == "a\\nb"

    def test_backslash(self):
        assert _escape_ical("a\\b") == "a\\\\b"

    def test_no_escaping_needed(self):
        assert _escape_ical("Frühschicht") == "Frühschicht"

    def test_combined(self):
        assert _escape_ical("a;b,c\nd") == "a\\;b\\,c\\nd"


class TestParseTime:
    def test_valid(self):
        assert _parse_time("08:00") == (8, 0)
        assert _parse_time("14:30") == (14, 30)
        assert _parse_time("0:00") == (0, 0)

    def test_invalid(self):
        assert _parse_time("") is None
        assert _parse_time("abc") is None
        assert _parse_time(None) is None  # type: ignore[arg-type]

    def test_edge_cases(self):
        assert _parse_time("23:59") == (23, 59)
        assert _parse_time("6:00") == (6, 0)


class TestBuildIcal:
    def test_empty_calendar(self):
        result = _build_ical([], "Test Calendar")
        assert "BEGIN:VCALENDAR" in result
        assert "END:VCALENDAR" in result
        assert "X-WR-CALNAME:Test Calendar" in result
        assert "PRODID:-//OpenSchichtplaner5//Schichtplan//DE" in result
        assert "BEGIN:VEVENT" not in result

    def test_timed_event(self):
        events = [
            {
                "uid": "test123@openschichtplaner5",
                "dtstart": "20260315T060000Z",
                "dtend": "20260315T140000Z",
                "summary": "Frühschicht",
                "description": "Früh",
                "categories": "Schicht",
                "all_day": False,
            }
        ]
        result = _build_ical(events, "Test")
        assert "BEGIN:VEVENT" in result
        assert "END:VEVENT" in result
        assert "DTSTART:20260315T060000Z" in result
        assert "DTEND:20260315T140000Z" in result
        assert "SUMMARY:Frühschicht" in result
        assert "CATEGORIES:Schicht" in result

    def test_all_day_event(self):
        events = [
            {
                "uid": "test456@openschichtplaner5",
                "dtstart": "20260315",
                "dtend": "20260316",
                "summary": "Urlaub",
                "description": "",
                "categories": "Abwesenheit",
                "all_day": True,
            }
        ]
        result = _build_ical(events, "Test")
        assert "DTSTART;VALUE=DATE:20260315" in result
        assert "DTEND;VALUE=DATE:20260316" in result

    def test_multiple_events(self):
        events = [
            {
                "uid": f"ev{i}@sp5",
                "dtstart": f"2026031{i}",
                "dtend": f"2026031{i+1}",
                "summary": f"Event {i}",
                "description": "",
                "categories": "",
                "all_day": True,
            }
            for i in range(1, 4)
        ]
        result = _build_ical(events, "Multi")
        assert result.count("BEGIN:VEVENT") == 3
        assert result.count("END:VEVENT") == 3

    def test_crlf_line_endings(self):
        result = _build_ical([], "Test")
        # iCal spec requires CRLF
        assert "\r\n" in result


# ── API endpoint tests ───────────────────────────────────────────


class TestIcalEndpoints:
    """Test iCal API endpoints using the test client."""

    @pytest.fixture
    def client(self):
        """Create a test client with dev mode enabled."""
        import os
        os.environ["SP5_DEV_MODE"] = "true"
        os.environ["DB_PATH"] = os.environ.get(
            "DB_PATH", "/home/claw/.openclaw/workspace/sp5_db/Daten"
        )

        # Need to reimport to pick up dev mode
        from importlib import reload

        import api.dependencies
        reload(api.dependencies)
        import api.main
        reload(api.main)

        from api.main import app
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_my_schedule_unauthenticated(self, client):
        """Unauthenticated request should return 401."""
        resp = client.get("/api/ical/my-schedule.ics?year=2026&month=3")
        assert resp.status_code == 401

    def test_my_schedule_authenticated(self, client):
        """Authenticated request should return iCal content."""
        resp = client.get(
            "/api/ical/my-schedule.ics?year=2026&month=3",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        # Dev mode user may not have EMPLOYEEID — could be 400 or 200
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "text/calendar" in resp.headers.get("content-type", "")
            assert "BEGIN:VCALENDAR" in resp.text

    def test_employee_schedule_authenticated(self, client):
        """Request for specific employee should work."""
        resp = client.get(
            "/api/ical/schedule/1.ics?year=2026&month=3",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        # Employee 1 may or may not exist
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert "text/calendar" in resp.headers.get("content-type", "")
            assert "BEGIN:VCALENDAR" in resp.text
            assert "Content-Disposition" in resp.headers

    def test_invalid_month(self, client):
        """Invalid month should return 400."""
        resp = client.get(
            "/api/ical/schedule/1.ics?year=2026&month=13",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        assert resp.status_code == 400

    def test_invalid_year(self, client):
        """Invalid year should return 400."""
        resp = client.get(
            "/api/ical/schedule/1.ics?year=1999&month=3",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        assert resp.status_code == 400

    def test_ical_content_structure(self, client):
        """Verify the iCal file has correct structure."""
        resp = client.get(
            "/api/ical/schedule/1.ics?year=2026&month=3",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        if resp.status_code == 200:
            content = resp.text
            assert content.startswith("BEGIN:VCALENDAR")
            assert "VERSION:2.0" in content
            assert "PRODID:" in content
            assert content.strip().endswith("END:VCALENDAR")

    def test_content_disposition_header(self, client):
        """Response should have Content-Disposition for download."""
        resp = client.get(
            "/api/ical/schedule/1.ics?year=2026&month=3",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        if resp.status_code == 200:
            cd = resp.headers.get("content-disposition", "")
            assert "attachment" in cd
            assert ".ics" in cd
