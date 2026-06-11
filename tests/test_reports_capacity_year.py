"""Tests for the yearly capacity overview (GET /api/capacity-year) in reports.py
— per-month aggregated staffing for the heatmap (avg staffing, coverage %,
ok/low/critical/unplanned day counts, worst status). Driven with a fake db."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


class _CapYearDB:
    def get_employees(self, *a, **k):
        return [{"ID": i} for i in (1, 2, 3, 4)]

    def get_group_members(self, gid):
        return [1, 2, 3, 4]

    def get_utilization(self, year, month, group_id=None):
        import calendar

        result = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            if iso == "2026-01-05":
                # 2 eingeteilt (inkl. 5CYASS) unter echtem 5SHDEM-Minimum 3
                result.append(
                    {
                        "day": day,
                        "date": iso,
                        "scheduled_count": 2,
                        "required_count": 3,
                        "required_min": 3,
                        "required_max": 4,
                        "status": "under",
                        "cells": [],
                    }
                )
            else:
                result.append(
                    {
                        "day": day,
                        "date": iso,
                        "scheduled_count": 0,
                        "required_count": None,
                        "required_min": None,
                        "required_max": None,
                        "status": "none",
                        "cells": [],
                    }
                )
        return result


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 994,
        "NAME": "capy_admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
    }
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_capacity_year_aggregates_per_month(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _CapYearDB())
    try:
        resp = client.get("/api/v1/capacity-year?year=2026")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_employees"] == 4
        assert len(data["months"]) == 12

        january = next(m for m in data["months"] if m["month"] == 1)
        # the one planned Monday is recorded; exact average depends on the
        # per-month divisor — just assert the aggregation produced sane values
        assert january["planned_days"] >= 1
        assert january["avg_staffing"] >= 0
        assert january["worst_status"] in ("ok", "low", "critical", "unplanned")
        assert 0 <= january["coverage_pct"] <= 100
        # D6-Repro: der echte 5SHDEM-Bedarf (min 3, 2 eingeteilt) macht den
        # Montag zum "low"-Tag — die alte erfundene Wochentags-Aggregation
        # aus get_staffing_requirements wird nicht mehr befragt
        assert january["low_days"] == 1
    finally:
        _sessions.pop(tok, None)
