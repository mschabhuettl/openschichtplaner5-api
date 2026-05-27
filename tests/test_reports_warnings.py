"""Tests for the Warnings Center endpoint (GET /api/warnings) in reports.py.
It aggregates four warning types (unplanned next month, overtime over
threshold, understaffing, shift+absence conflicts), each guarded so one
failing source can't break the others. Driven with a fake db that triggers
all four at once."""

import secrets

import api.routers.reports as reports
from starlette.testclient import TestClient


class _WarnDB:
    def _read(self, name):
        return []  # no schedule entries → next month unplanned + understaffing

    def get_statistics(self, year, month):
        return [{"employee_name": "Max Muster", "employee_id": 1, "overtime_hours": 25.0}]

    def get_staffing_requirements(self):
        return {
            "shift_requirements": [{"weekday": 0, "min": 2, "shift_id": 1, "shift_name": "Früh"}]
        }

    def get_schedule_conflicts(self, year, month):
        return [
            {
                "employee_name": "Eva Test",
                "employee_id": 2,
                "date": "2020-01-15",
                "message": "Schicht + Abwesenheit",
            }
        ]


def _admin_session():
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 960, "NAME": "warnuser", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    return tok


class TestWarningsCenter:
    def test_aggregates_all_warning_types(self, monkeypatch):
        from api.main import _sessions, app

        monkeypatch.setattr(reports, "get_db", lambda: _WarnDB())
        tok = _admin_session()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok
            # A past month → days-until-end is negative (<7) → "next month unplanned" fires,
            # and the month iteration runs for the understaffing check.
            r = c.get("/api/v1/warnings?year=2020&month=1")
            assert r.status_code == 200
            data = r.json()
            types = {w["type"] for w in data["warnings"]}
            assert "next_month_unplanned" in types
            assert "overtime_exceeded" in types
            assert "understaffing" in types
            assert "conflict" in types
            assert data["count"] == len(data["warnings"])
        finally:
            _sessions.pop(tok, None)

    def test_invalid_month_returns_400(self, monkeypatch):
        from api.main import _sessions, app

        monkeypatch.setattr(reports, "get_db", lambda: _WarnDB())
        tok = _admin_session()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.headers["X-Auth-Token"] = tok
            r = c.get("/api/v1/warnings?year=2020&month=13")
            assert r.status_code == 400
        finally:
            _sessions.pop(tok, None)
