"""Covers the two data-dependent branches of /api/dashboard/upcoming that the
basic smoke test doesn't reach: the recurring-holiday expansion (only runs when
there are no future-dated holidays) and the birthdays-this-week computation."""

import secrets
from datetime import date

import api.main as main
from starlette.testclient import TestClient

_TODAY = date.today()


class _UpcomingDB:
    def get_holidays(self, year=None):
        # Both dates are in the past → no future holidays → the recurring-expansion
        # branch runs. One expands into the past-this-year (→ bumped to next year),
        # one into the future-this-year.
        return [
            {"DATE": "2000-01-01", "NAME": "Neujahr", "INTERVAL": 1},
            {"DATE": "2000-12-25", "NAME": "Weihnachten", "INTERVAL": 1},
        ]

    def get_employees(self, include_hidden=False):
        # Birthday today → falls inside the current Mon–Sun week.
        bday = f"1990-{_TODAY.month:02d}-{_TODAY.day:02d}"
        return [{"ID": 1, "NAME": "Berg", "FIRSTNAME": "Anna", "SHORTNAME": "AB", "BIRTHDAY": bday}]


def test_upcoming_recurring_holidays_and_birthdays(monkeypatch):
    monkeypatch.setattr(main, "get_db", lambda: _UpcomingDB())
    tok = secrets.token_hex(20)
    main._sessions[tok] = {"ID": 998, "NAME": "up", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/upcoming", headers={"X-Auth-Token": tok})
        assert resp.status_code == 200
        data = resp.json()
        # recurring holidays were expanded into the current/next year
        assert len(data["holidays"]) >= 1
        assert all(h["recurring"] for h in data["holidays"])
        # the employee whose birthday is today shows up this week
        assert len(data["birthdays_this_week"]) == 1
        assert data["birthdays_this_week"][0]["employee_id"] == 1
    finally:
        main._sessions.pop(tok, None)
