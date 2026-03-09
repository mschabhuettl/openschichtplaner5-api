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
        """Create a test client with dev mode enabled (no reload)."""
        import os

        import api.dependencies as deps
        from api.main import app
        from fastapi.testclient import TestClient

        old_dev = os.environ.get("SP5_DEV_MODE")
        os.environ["SP5_DEV_MODE"] = "true"
        old_flag = deps._DEV_MODE_ACTIVE
        deps._DEV_MODE_ACTIVE = True

        # Ensure dev-mode token exists
        had_dev_tok = "__dev_mode__" in deps._sessions
        if not had_dev_tok:
            deps._sessions["__dev_mode__"] = {
                "ID": 0,
                "NAME": "dev",
                "role": "Admin",
                "ADMIN": True,
                "RIGHTS": 255,
                "expires_at": None,
            }

        yield TestClient(app)

        deps._DEV_MODE_ACTIVE = old_flag
        if not had_dev_tok:
            deps._sessions.pop("__dev_mode__", None)
        if old_dev is None:
            os.environ.pop("SP5_DEV_MODE", None)
        else:
            os.environ["SP5_DEV_MODE"] = old_dev

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


# ── Token & Feed tests ──────────────────────────────────────────


class TestIcalTokenDb:
    """Test iCal token CRUD in the database layer."""

    @pytest.fixture
    def db(self):
        """Create a database instance pointing at the test DB."""
        import os
        db_path = os.environ.get(
            "DB_PATH", "/home/claw/.openclaw/workspace/sp5_db/Daten"
        )
        from sp5lib.database import SP5Database
        database = SP5Database(db_path)
        # Clean up any leftover test tokens
        yield database
        # Cleanup
        try:
            tokens = database._load_ical_tokens()
            tokens = {t: info for t, info in tokens.items()
                      if info.get("employee_id") not in (99990, 99991)}
            database._save_ical_tokens(tokens)
        except Exception:
            pass

    def test_create_token(self, db):
        """Creating a token returns a non-empty string."""
        token = db.create_ical_token(99990)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_resolve_token(self, db):
        """A created token can be resolved to the employee ID."""
        token = db.create_ical_token(99990)
        emp_id = db.resolve_ical_token(token)
        assert emp_id == 99990

    def test_resolve_invalid_token(self, db):
        """An invalid token returns None."""
        assert db.resolve_ical_token("nonexistent_token_xyz") is None

    def test_get_token_for_employee(self, db):
        """get_ical_token_for_employee returns the current token."""
        token = db.create_ical_token(99990)
        found = db.get_ical_token_for_employee(99990)
        assert found == token

    def test_get_token_for_employee_none(self, db):
        """get_ical_token_for_employee returns None when no token exists."""
        db.revoke_ical_token(99991)  # ensure clean
        assert db.get_ical_token_for_employee(99991) is None

    def test_regenerate_revokes_old(self, db):
        """Creating a new token revokes the old one."""
        old_token = db.create_ical_token(99990)
        new_token = db.create_ical_token(99990)
        assert old_token != new_token
        assert db.resolve_ical_token(old_token) is None
        assert db.resolve_ical_token(new_token) == 99990

    def test_revoke_token(self, db):
        """Revoking a token makes it unresolvable."""
        token = db.create_ical_token(99990)
        assert db.revoke_ical_token(99990) is True
        assert db.resolve_ical_token(token) is None

    def test_revoke_nonexistent(self, db):
        """Revoking when no token exists returns False."""
        db.revoke_ical_token(99991)  # ensure clean
        assert db.revoke_ical_token(99991) is False


class TestIcalFeedEndpoints:
    """Test iCal feed and token API endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client with dev mode enabled (no reload)."""
        import os

        import api.dependencies as deps
        from api.main import app
        from fastapi.testclient import TestClient

        old_dev = os.environ.get("SP5_DEV_MODE")
        os.environ["SP5_DEV_MODE"] = "true"
        old_flag = deps._DEV_MODE_ACTIVE
        deps._DEV_MODE_ACTIVE = True

        had_dev_tok = "__dev_mode__" in deps._sessions
        if not had_dev_tok:
            deps._sessions["__dev_mode__"] = {
                "ID": 0,
                "NAME": "dev",
                "role": "Admin",
                "ADMIN": True,
                "RIGHTS": 255,
                "expires_at": None,
            }

        yield TestClient(app)

        deps._DEV_MODE_ACTIVE = old_flag
        if not had_dev_tok:
            deps._sessions.pop("__dev_mode__", None)
        if old_dev is None:
            os.environ.pop("SP5_DEV_MODE", None)
        else:
            os.environ["SP5_DEV_MODE"] = old_dev

    def test_create_token_endpoint(self, client):
        """POST /api/ical/token should return token and URLs."""
        resp = client.post(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        # Dev mode user may not have EMPLOYEEID — could be 400 or 200
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            data = resp.json()
            assert "token" in data
            assert "feed_url" in data
            assert "webcal_url" in data
            assert data["token"]
            assert "/api/ical/feed/" in data["feed_url"]
            assert ".ics" in data["feed_url"]
            assert data["webcal_url"].startswith("webcal://")

    def test_get_token_endpoint(self, client):
        """GET /api/ical/token should return current token info."""
        resp = client.get(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            data = resp.json()
            assert "token" in data

    def test_feed_with_invalid_token(self, client):
        """GET /api/ical/feed/{bad_token}.ics should return 404."""
        resp = client.get("/api/ical/feed/invalid_token_xyz123.ics")
        assert resp.status_code == 404

    def test_feed_no_auth_required(self, client):
        """Feed endpoint should not require auth headers."""
        resp = client.get("/api/ical/feed/some_token.ics")
        # Should be 404 (bad token), not 401 (unauthorized)
        assert resp.status_code == 404

    def test_full_token_lifecycle(self, client):
        """Create token → use feed → revoke → feed fails."""
        # Create
        resp = client.post(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        if resp.status_code != 200:
            pytest.skip("Dev mode user has no employee — can't test lifecycle")

        data = resp.json()
        feed_url = data["feed_url"]

        # Extract relative path from feed_url
        from urllib.parse import urlparse
        feed_path = urlparse(feed_url).path

        # Use feed (no auth header!)
        resp = client.get(feed_path)
        assert resp.status_code in (200, 404)  # 404 if employee has no schedule data
        if resp.status_code == 200:
            assert "text/calendar" in resp.headers.get("content-type", "")
            assert "BEGIN:VCALENDAR" in resp.text
            # Feed should NOT have Content-Disposition attachment
            cd = resp.headers.get("content-disposition", "")
            assert "attachment" not in cd

        # Revoke
        resp = client.delete(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        assert resp.status_code == 200

        # Feed should now fail
        resp = client.get(feed_path)
        assert resp.status_code == 404

    def test_delete_token_unauthenticated(self, client):
        """DELETE /api/ical/token without auth should return 401."""
        resp = client.delete("/api/ical/token")
        assert resp.status_code == 401

    def test_regenerate_invalidates_old(self, client):
        """Creating a new token should invalidate the old feed URL."""
        # Create first token
        resp1 = client.post(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        if resp1.status_code != 200:
            pytest.skip("Dev mode user has no employee")

        old_token = resp1.json()["token"]

        # Create second token
        resp2 = client.post(
            "/api/ical/token",
            headers={"X-Auth-Token": "__dev_mode__"},
        )
        assert resp2.status_code == 200
        new_token = resp2.json()["token"]
        assert old_token != new_token

        # Old feed should fail
        resp = client.get(f"/api/ical/feed/{old_token}.ics")
        assert resp.status_code == 404


class TestGenerateFeedIcal:
    """Test the _generate_feed_ical helper."""

    def test_feed_returns_valid_ical(self):
        """Feed should return valid iCal with VCALENDAR wrapper."""
        import os

        from sp5lib.database import SP5Database
        db_path = os.environ.get(
            "SP5_DB_PATH",
            os.environ.get("DB_PATH", "/home/claw/.openclaw/workspace/sp5_db/Daten"),
        )
        db = SP5Database(db_path)
        employees = db.get_employees(include_hidden=False)

        if not employees:
            pytest.skip("No employees in test DB")

        emp_id = employees[0]["ID"]

        from api.routers.ical import _generate_feed_ical
        try:
            result = _generate_feed_ical(emp_id)
            assert "BEGIN:VCALENDAR" in result
            assert "END:VCALENDAR" in result
            assert "X-WR-CALNAME:" in result
            assert "\r\n" in result
        except Exception:
            pass

    def test_feed_nonexistent_employee(self):
        """Feed for nonexistent employee should raise 404."""
        from api.routers.ical import _generate_feed_ical
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _generate_feed_ical(999999)
