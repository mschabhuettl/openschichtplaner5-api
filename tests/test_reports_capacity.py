"""Tests for the capacity-forecast analytics (GET /api/capacity-forecast) in
reports.py — the per-day coverage computation (scheduled vs. required, absences,
conflict flag) which the fixtures don't populate. Driven with a fake db."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports

_MONDAY = "2026-01-05"  # a Monday (weekday 0)


class _CapDB:
    def get_employees(self, *a, **k):
        return [{"ID": i, "FIRSTNAME": f"E{i}", "NAME": "X"} for i in (1, 2, 3, 4)]

    def get_group_members(self, gid):
        return [1, 2, 3, 4]

    def get_leave_types(self):
        return [{"ID": 1, "SHORTNAME": "U"}]

    def get_schedule(self, year, month, group_id=None):
        if (year, month) != (2026, 1):
            return []
        return [
            {"employee_id": 1, "date": _MONDAY, "kind": "shift", "shift_id": 1},
            {"employee_id": 2, "date": _MONDAY, "kind": "shift", "shift_id": 1},
            # 5CYASS-expandierter Zyklusdienst am Di — Roh-MASHI-Leser übersehen ihn
            {
                "employee_id": 3,
                "date": "2026-01-06",
                "kind": "shift",
                "source": "cycle",
                "shift_id": 1,
            },
            {"employee_id": 3, "date": _MONDAY, "kind": "absence", "leave_type_id": 1},
            {"employee_id": 4, "date": _MONDAY, "kind": "absence", "leave_type_id": 1},
        ]

    def get_utilization(self, year, month, group_id=None):
        import calendar

        result = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            # echter 5SHDEM-Bedarf nur am Montag 2026-01-05: min=3
            required = 3 if iso == _MONDAY else None
            result.append(
                {
                    "day": day,
                    "date": iso,
                    "scheduled_count": 0,
                    "required_count": required,
                    "required_min": required,
                    "required_max": required,
                    "status": "under" if required else "none",
                    "cells": [],
                }
            )
        return result


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 980, "NAME": "cap_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_capacity_forecast_computes_coverage_and_conflict(monkeypatch):
    from sp5api.main import _sessions

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

        # D6-Repro: Zyklusdienste (5CYASS) zählen als geplant — der alte
        # Roh-MASHI-Leser hätte am Di 0 Eingeteilte gemeldet
        tuesday = next(d for d in data["days"] if d["date"] == "2026-01-06")
        assert tuesday["scheduled_count"] == 1
    finally:
        _sessions.pop(tok, None)


def test_capacity_forecast_invalid_month_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _CapDB())
    try:
        resp = client.get("/api/v1/capacity-forecast?year=2026&month=13")
        assert resp.status_code == 400
    finally:
        _sessions.pop(tok, None)
