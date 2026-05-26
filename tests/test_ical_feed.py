"""Unit tests for ical helpers and the rolling-feed generator.

_generate_feed_ical builds a multi-month iCal feed from db.get_schedule data.
It's driven here with a fake db so the shift (all-day fallback) and absence
event-building branches, plus the date-parse skips, are exercised directly."""

from datetime import date

import api.routers.ical as ical


class TestParseTime:
    def test_valid(self):
        assert ical._parse_time("06:30") == (6, 30)

    def test_empty_or_malformed(self):
        assert ical._parse_time("") is None  # guard: falsy
        assert ical._parse_time("not-a-time") is None  # guard: no colon
        assert ical._parse_time("ab:cd") is None  # has colon but non-numeric → except


class _FakeDB:
    def __init__(self, employee, schedule):
        self._employee = employee
        self._schedule = schedule

    def get_employee(self, eid):
        return self._employee

    def get_shifts(self, include_hidden=True):
        return []  # unknown shift ids → all-day fallback path

    def get_leave_types(self):
        return [{"ID": 1, "NAME": "Urlaub"}]

    def get_schedule(self, year, month):
        # Serve the entries only in the current month so they appear once.
        today = date.today()
        return self._schedule if (year, month) == (today.year, today.month) else []


def test_feed_builds_shift_and_absence_events(monkeypatch):
    today = date.today()
    day = date(today.year, today.month, 15).isoformat()
    schedule = [
        # shift with no resolvable time → all-day fallback event
        {
            "employee_id": 5,
            "date": day,
            "kind": "shift",
            "shift_id": 999,
            "shift_name": "Frühdienst",
        },
        # absence → absence event
        {"employee_id": 5, "date": day, "kind": "absence", "leave_type_id": 1},
        # skipped rows
        {"employee_id": 5, "date": "", "kind": "shift"},  # blank date
        {"employee_id": 5, "date": "2026-13-99", "kind": "shift"},  # invalid date
        {"employee_id": 999, "date": day, "kind": "shift", "shift_id": 999},  # other emp
    ]
    db = _FakeDB({"ID": 5, "FIRSTNAME": "Anna", "NAME": "Berg"}, schedule)
    monkeypatch.setattr(ical, "get_db", lambda: db)

    ics = ical._generate_feed_ical(5)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "Schichtplan Anna Berg" in ics
    # both an all-day shift event and the absence event are present
    assert ics.count("BEGIN:VEVENT") == 2
    assert "Urlaub" in ics  # absence summary from the leave type


def test_feed_unknown_employee_raises_404(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(ical, "get_db", lambda: _FakeDB(None, []))
    with pytest.raises(HTTPException) as exc:
        ical._generate_feed_ical(123)
    assert exc.value.status_code == 404


class TestTokenLifecycle:
    """Full create → get → public-feed → revoke flow for a real employee."""

    def _session(self, employee_id=40):
        import secrets

        from api.main import _sessions

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": employee_id,
            "NAME": "icaluser",
            "role": "Admin",
            "ADMIN": True,
            "RIGHTS": 255,
            "EMPLOYEEID": employee_id,
            "employee_id": employee_id,
        }
        return tok

    def test_token_create_get_feed_revoke(self, write_db_path):
        from api.main import _sessions, app
        from starlette.testclient import TestClient

        tok = self._session(40)  # employee 40 exists in the fixtures
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok

            # create — builds feed_url / webcal_url
            r = c.post("/api/ical/token")
            assert r.status_code == 200
            body = r.json()
            feed_token = body["token"]
            assert feed_token
            assert body["feed_url"].endswith(f"/api/ical/feed/{feed_token}.ics")
            assert body["webcal_url"].startswith("webcal://")

            # get — returns the same token + urls
            r2 = c.get("/api/ical/token")
            assert r2.status_code == 200
            assert r2.json()["token"] == feed_token

            # public feed — no auth required
            pub = TestClient(app, raise_server_exceptions=False)
            r3 = pub.get(f"/api/ical/feed/{feed_token}.ics")
            assert r3.status_code == 200
            assert "BEGIN:VCALENDAR" in r3.text

            # revoke — token now gone
            r4 = c.delete("/api/ical/token")
            assert r4.status_code == 200
            assert r4.json()["ok"] is True

            # revoking again → "no token" branch
            r5 = c.delete("/api/ical/token")
            assert r5.status_code == 200
            assert r5.json()["ok"] is True
        finally:
            _sessions.pop(tok, None)
