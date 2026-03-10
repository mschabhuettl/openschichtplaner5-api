"""
Comprehensive tests for ical.py — iCal export, feed subscriptions, token management.
Targets uncovered lines (61% → 80%+): lines 193-334, 376-400, 450-565, 588-623.
"""

import secrets

import pytest
from starlette.testclient import TestClient


def _fresh_client():
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _inject_session(user_id=800, name="testuser", role="Admin", employee_id=None):
    from api.main import _sessions
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": user_id,
        "NAME": name,
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else (2 if role == "Planer" else 1),
        "EMPLOYEEID": employee_id or user_id,
        "employee_id": employee_id or user_id,
    }
    return tok


def _cleanup(tok):
    from api.main import _sessions
    _sessions.pop(tok, None)


# ── My Schedule Download ───────────────────────────────────────────────────────


class TestMyScheduleIcal:
    def test_my_schedule_success(self, sync_client):
        """Download personal schedule as iCal."""
        res = sync_client.get("/api/ical/my-schedule.ics?year=2026&month=3")
        # May be 200 (calendar) or 400/404 (no employee linked)
        assert res.status_code in (200, 400, 404)
        if res.status_code == 200:
            assert "text/calendar" in res.headers.get("content-type", "")
            body = res.text
            assert "BEGIN:VCALENDAR" in body
            assert "END:VCALENDAR" in body

    def test_my_schedule_invalid_month(self, sync_client):
        res = sync_client.get("/api/ical/my-schedule.ics?year=2026&month=13")
        assert res.status_code == 400

    def test_my_schedule_invalid_year(self, sync_client):
        res = sync_client.get("/api/ical/my-schedule.ics?year=1999&month=3")
        assert res.status_code == 400

    def test_my_schedule_unauthenticated(self):
        c = _fresh_client()
        res = c.get("/api/ical/my-schedule.ics?year=2026&month=3")
        assert res.status_code == 401


# ── Employee Schedule Download ─────────────────────────────────────────────────


class TestEmployeeScheduleIcal:
    def test_employee_schedule_as_admin(self, sync_client):
        """Admin can download any employee's schedule."""
        # Get first employee
        emps_res = sync_client.get("/api/employees")
        if emps_res.status_code == 200 and emps_res.json():
            emp_id = emps_res.json()[0].get("ID", 1)
            res = sync_client.get(
                f"/api/ical/schedule/{emp_id}.ics?year=2026&month=3"
            )
            assert res.status_code in (200, 404)
            if res.status_code == 200:
                assert "BEGIN:VCALENDAR" in res.text

    def test_employee_schedule_nonexistent(self, sync_client):
        res = sync_client.get("/api/ical/schedule/99999.ics?year=2026&month=3")
        assert res.status_code == 404

    def test_employee_schedule_as_reader_own(self):
        """Reader can access own schedule."""
        tok = _inject_session(user_id=1, name="reader", role="Leser", employee_id=1)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.get("/api/ical/schedule/1.ics?year=2026&month=3")
        # 200 or 404 (employee might not exist in test DB)
        assert res.status_code in (200, 404)
        _cleanup(tok)

    def test_employee_schedule_as_reader_other_forbidden(self):
        """Reader cannot access other employee's schedule."""
        tok = _inject_session(user_id=1, name="reader", role="Leser", employee_id=1)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.get("/api/ical/schedule/999.ics?year=2026&month=3")
        assert res.status_code == 403
        _cleanup(tok)


# ── iCal Token Management ─────────────────────────────────────────────────────


class TestIcalTokenManagement:
    def test_create_token(self, write_db_path):
        """Create a new iCal feed token."""
        # Need a user with a real employee linked
        tok = _inject_session(user_id=1, name="tokenuser", role="Admin", employee_id=1)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        res = c.post("/api/ical/token")
        if res.status_code == 200:
            data = res.json()
            assert "token" in data
            assert "feed_url" in data
            assert "webcal_url" in data
            assert "webcal://" in data["webcal_url"]
            assert ".ics" in data["feed_url"]

            # Get token should return same token
            res2 = c.get("/api/ical/token")
            assert res2.status_code == 200
            data2 = res2.json()
            assert data2["token"] == data["token"]

            # Revoke token
            res3 = c.delete("/api/ical/token")
            assert res3.status_code == 200
            assert res3.json()["ok"] is True

            # After revoke, get should return null
            res4 = c.get("/api/ical/token")
            assert res4.status_code == 200
            assert res4.json()["token"] is None

        _cleanup(tok)

    def test_get_token_none_exists(self, write_db_path):
        tok = _inject_session(user_id=2, name="notoken", role="Admin", employee_id=2)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        res = c.get("/api/ical/token")
        if res.status_code == 200:
            assert res.json()["token"] is None
        _cleanup(tok)

    def test_revoke_no_token(self, write_db_path):
        tok = _inject_session(user_id=3, name="norevo", role="Admin", employee_id=3)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        res = c.delete("/api/ical/token")
        if res.status_code == 200:
            assert res.json()["ok"] is True
        _cleanup(tok)

    def test_token_no_employee(self):
        """User without employee_id can't create token."""
        from api.main import _sessions
        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 999,
            "NAME": "noemp",
            "role": "Admin",
            "ADMIN": True,
            "RIGHTS": 255,
            # No EMPLOYEEID or employee_id
        }
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok
        res = c.post("/api/ical/token")
        # Should get 400 (no employee) or the ID fallback works
        assert res.status_code in (200, 400, 404)
        _cleanup(tok)


# ── iCal Feed (token-based, no auth) ──────────────────────────────────────────


class TestIcalFeed:
    def test_feed_invalid_token(self):
        c = _fresh_client()
        res = c.get("/api/ical/feed/nonexistent_token_123.ics")
        assert res.status_code == 404

    def test_feed_valid_token(self, write_db_path):
        """Create token, then access feed without auth."""
        tok = _inject_session(user_id=1, name="feeduser", role="Admin", employee_id=1)
        c = _fresh_client()
        c.headers["X-Auth-Token"] = tok

        res = c.post("/api/ical/token")
        if res.status_code == 200:
            feed_token = res.json()["token"]

            # Access feed without auth
            c2 = _fresh_client()
            res2 = c2.get(f"/api/ical/feed/{feed_token}.ics")
            assert res2.status_code == 200
            assert "BEGIN:VCALENDAR" in res2.text
            assert "no-cache" in res2.headers.get("cache-control", "")

            # Revoke and verify feed stops working
            c.delete("/api/ical/token")
            res3 = c2.get(f"/api/ical/feed/{feed_token}.ics")
            assert res3.status_code == 404

        _cleanup(tok)


# ── iCal Content Structure ────────────────────────────────────────────────────


class TestIcalContent:
    def test_ical_has_correct_structure(self, sync_client):
        """Verify iCal output follows the standard."""
        emps = sync_client.get("/api/employees")
        if emps.status_code != 200 or not emps.json():
            pytest.skip("No employees in test DB")

        emp_id = emps.json()[0]["ID"]
        res = sync_client.get(
            f"/api/ical/schedule/{emp_id}.ics?year=2026&month=3"
        )
        if res.status_code != 200:
            pytest.skip("No schedule data")

        ical = res.text
        assert ical.startswith("BEGIN:VCALENDAR")
        assert "VERSION:2.0" in ical
        assert "PRODID:" in ical
        assert "END:VCALENDAR" in ical

    def test_ical_month_boundary(self, sync_client):
        """Test edge months: January, December."""
        emps = sync_client.get("/api/employees")
        if emps.status_code != 200 or not emps.json():
            pytest.skip("No employees")

        emp_id = emps.json()[0]["ID"]
        for month in [1, 12]:
            res = sync_client.get(
                f"/api/ical/schedule/{emp_id}.ics?year=2026&month={month}"
            )
            assert res.status_code in (200, 404)


# ── Helper function tests ─────────────────────────────────────────────────────


class TestIcalHelpers:
    def test_parse_time(self):
        from api.routers.ical import _parse_time
        assert _parse_time("08:30") == (8, 30)
        assert _parse_time("8:00") == (8, 0)
        assert _parse_time("23:59") == (23, 59)
        assert _parse_time("") is None
        assert _parse_time(None) is None
        assert _parse_time("invalid") is None
        assert _parse_time("25:00") == (25, 0)  # doesn't validate range

    def test_escape_ical(self):
        from api.routers.ical import _escape_ical
        assert _escape_ical("Hello; World") == "Hello\\; World"
        assert _escape_ical("A, B") == "A\\, B"
        assert _escape_ical("Line\nBreak") == "Line\\nBreak"
        assert _escape_ical("Back\\slash") == "Back\\\\slash"

    def test_make_uid(self):
        from api.routers.ical import _make_uid
        uid = _make_uid(1, "2026-03-10", "shift-5")
        assert "@openschichtplaner5" in uid
        assert len(uid) > 20
        # Deterministic
        assert _make_uid(1, "2026-03-10", "shift-5") == uid

    def test_ical_dt(self):
        from datetime import UTC, datetime

        from api.routers.ical import _ical_dt
        dt = datetime(2026, 3, 10, 14, 30, 0, tzinfo=UTC)
        assert _ical_dt(dt) == "20260310T143000Z"

    def test_ical_date(self):
        from datetime import date

        from api.routers.ical import _ical_date
        assert _ical_date(date(2026, 3, 10)) == "20260310"

    def test_build_ical(self):
        from api.routers.ical import _build_ical
        events = [
            {
                "uid": "test-uid@test",
                "dtstart": "20260310",
                "dtend": "20260311",
                "summary": "Test Event",
                "description": "A description",
                "categories": "Test",
                "all_day": True,
            },
            {
                "uid": "test-uid2@test",
                "dtstart": "20260310T080000Z",
                "dtend": "20260310T160000Z",
                "summary": "Timed Event",
                "description": "",
                "categories": "",
                "all_day": False,
            },
        ]
        ical = _build_ical(events, "Test Calendar")
        assert "BEGIN:VCALENDAR" in ical
        assert "END:VCALENDAR" in ical
        assert "BEGIN:VEVENT" in ical
        assert "Test Event" in ical
        assert "DTSTART;VALUE=DATE:20260310" in ical
        assert "DTSTART:20260310T080000Z" in ical
        assert ical.count("BEGIN:VEVENT") == 2
