"""Tests for the capacity-forecast analytics (GET /api/capacity-forecast) in
reports.py — the per-day coverage computation (scheduled vs. required, absences,
conflict flag) which the fixtures don't populate. Driven with a fake db."""

import secrets

import api.routers.reports as reports
from starlette.testclient import TestClient

_MONDAY = "2026-01-05"  # a Monday (weekday 0)


class _CapDB:
    def get_employees(self, *a, **k):
        return [{"ID": i, "FIRSTNAME": f"E{i}", "NAME": "X"} for i in (1, 2, 3, 4)]

    def get_group_members(self, gid):
        return [1, 2, 3, 4]

    def get_leave_types(self):
        return [{"ID": 1, "SHORTNAME": "U"}]

    def get_staffing_requirements(self):
        return {"shift_requirements": [{"weekday": 0, "min": 3}]}  # Monday needs 3

    def _read(self, name):
        if name == "MASHI":
            return [
                {"DATE": _MONDAY, "EMPLOYEEID": 1, "SHIFTID": 1},
                {"DATE": _MONDAY, "EMPLOYEEID": 2, "SHIFTID": 1},
            ]
        if name == "ABSEN":
            return [
                {"DATE": _MONDAY, "EMPLOYEEID": 3, "LEAVETYPID": 1},
                {"DATE": _MONDAY, "EMPLOYEEID": 4, "LEAVETYPID": 1},
            ]
        return []  # SPSHI etc.


def _admin_client(monkeypatch, db):
    from api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 980, "NAME": "cap_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_capacity_forecast_computes_coverage_and_conflict(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _CapDB())
    try:
        resp = client.get("/api/v1/capacity-forecast?year=2026&month=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_employees"] == 4
        assert len(data["days"]) == 31

        monday = next(d for d in data["days"] if d["date"] == _MONDAY)
        # 2 scheduled vs required 3 → diff -1 → "low"; 2 of 4 absent → conflict flag
        assert monday["scheduled_count"] == 2
        assert monday["required_min"] == 3
        assert monday["coverage_status"] == "low"
        assert monday["absent_count"] == 2
        assert monday["conflict_flag"] is True

        # days with no schedule and no requirement → "unplanned"
        assert data["summary"]["unplanned_count"] >= 1
    finally:
        _sessions.pop(tok, None)


def test_capacity_forecast_invalid_month_400(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _CapDB())
    try:
        resp = client.get("/api/v1/capacity-forecast?year=2026&month=13")
        assert resp.status_code == 400
    finally:
        _sessions.pop(tok, None)
