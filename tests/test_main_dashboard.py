"""Smoke/coverage tests for the dashboard analytics endpoints in api/main.py
(summary, today, stats, upcoming). These large aggregation endpoints had no
direct coverage; a permissive fake db with minimal data drives them end to end."""

import secrets
from datetime import date

import api.main as main
from starlette.testclient import TestClient

_TODAY = date.today()
_PREFIX = f"{_TODAY.year:04d}-{_TODAY.month:02d}"
_TODAY_STR = _TODAY.isoformat()


class _DashDB:
    def get_employees(self, include_hidden=False):
        return [{"ID": 1, "NAME": "Berg", "FIRSTNAME": "Anna", "SHORTNAME": "AB", "GROUPID": 10}]

    def get_groups(self, include_hidden=False):
        return [{"ID": 10, "NAME": "Team A"}]

    def get_shifts(self, include_hidden=True):
        return [{"ID": 1, "NAME": "Früh", "SHORTNAME": "F", "STARTEND0": "06:00-14:00"}]

    def get_leave_types(self, include_hidden=True):
        return [{"ID": 1, "NAME": "Urlaub", "SHORTNAME": "U"}]

    def get_schedule_day(self, date_str):
        return [
            {
                "employee_id": 1,
                "employee_name": "Anna Berg",
                "employee_short": "AB",
                "kind": "shift",
                "shift_id": 1,
                "shift_name": "Früh",
                "shift_short": "F",
                "color_bk": "#ffffff",
                "color_text": "#000000",
                "leave_name": "",
                "custom_name": "",
                "display_name": "Früh",
                "date": date_str,
            }
        ]

    def _read(self, name):
        if name == "MASHI":
            return [{"DATE": _TODAY_STR, "EMPLOYEEID": 1, "SHIFTID": 1}]
        if name == "SPSHI":
            return [{"DATE": _TODAY_STR, "EMPLOYEEID": 1, "SHIFTID": 1, "TYPE": 0}]
        if name == "ABSEN":
            return [{"DATE": _TODAY_STR, "EMPLOYEEID": 1, "LEAVETYPID": 1, "LEAVETYPEID": 1}]
        return []

    def get_statistics(self, year, month, group_id=None):
        return [
            {
                "employee_id": 1,
                "employee_name": "Anna Berg",
                "employee_short": "AB",
                "overtime_hours": 5.0,
                "target_hours": 160.0,
                "actual_hours": 165.0,
                "absence_days": 0,
            }
        ]

    def get_staffing_requirements(self):
        return {"shift_requirements": [{"weekday": _TODAY.weekday(), "min": 1, "shift_id": 1}]}

    def get_holiday_dates(self, year):
        return []

    def get_holidays(self, year=None):
        return [{"DATE": f"{_TODAY.year}-12-25", "NAME": "Weihnachten"}]


def _client(monkeypatch):
    monkeypatch.setattr(main, "get_db", lambda: _DashDB())
    tok = secrets.token_hex(20)
    main._sessions[tok] = {"ID": 997, "NAME": "dash", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(main.app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def _check(monkeypatch, path):
    client, tok = _client(monkeypatch)
    try:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}: {resp.text[:300]}"
        assert isinstance(resp.json(), dict)
    finally:
        main._sessions.pop(tok, None)


def test_dashboard_summary(monkeypatch):
    _check(monkeypatch, "/api/v1/dashboard/summary")


def test_dashboard_today(monkeypatch):
    _check(monkeypatch, "/api/v1/dashboard/today")


def test_dashboard_stats(monkeypatch):
    _check(monkeypatch, "/api/v1/dashboard/stats")


def test_dashboard_upcoming(monkeypatch):
    _check(monkeypatch, "/api/v1/dashboard/upcoming")
