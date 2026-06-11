"""Tests for the monthly quality report (GET /api/quality-report) in reports.py
— per-day staffing coverage, hours-compliance issues, the weighted quality
score/grade and the findings list. Driven with a fake db."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports

_PREFIX = "2026-01"


class _QualDB:
    """Fake-Fassade: 2 aktive MA; eingeteilt nur am Mo 2026-01-05 (2 MA),
    dort echter 5SHDEM-Bedarf min=3 ⇒ Unterbesetzung trotz 2 Eingeteilten."""

    def _read(self, name):
        if name == "EMPL":
            return [{"ID": 1, "HIDE": 0}, {"ID": 2, "HIDE": 0}]
        return []

    def get_utilization(self, year, month, group_id=None):
        import calendar

        result = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            if iso == "2026-01-05":
                result.append(
                    {
                        "day": day,
                        "date": iso,
                        "scheduled_count": 2,
                        "required_count": 3,
                        "required_min": 3,
                        "required_max": 5,
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

    def get_statistics(self, year, month):
        # employee 1 significantly over target → an "over" hours issue
        return [
            {
                "employee_id": 1,
                "employee_name": "Anna Berg",
                "employee_short": "AB",
                "target_hours": 160.0,
                "actual_hours": 200.0,
                "absence_days": 0,
                "shifts_count": 20,
            }
        ]


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 990,
        "NAME": "qual_admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
    }
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_quality_report_scores_and_findings(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _QualDB())
    try:
        resp = client.get("/api/v1/quality-report?year=2026&month=1")
        assert resp.status_code == 200
        data = resp.json()
        # overall score + grade are computed
        assert isinstance(data["overall_score"], int)
        assert data["grade"] in ("A", "B", "C", "D")
        # 31 days in January, each with a coverage status
        assert len(data["coverage_days"]) == 31
        # the +25% hours deviation surfaces as an "over" issue (nested under "hours")
        assert any(h["issue_type"] == "over" for h in data["hours"]["issues"])
        assert len(data["findings"]) >= 1
    finally:
        _sessions.pop(tok, None)


def test_quality_report_uses_real_demand(monkeypatch):
    """D4-Repro: Besetzungs-Check muss den echten 5SHDEM-Bedarf nutzen.

    Am 2026-01-05 sind 2 MA eingeteilt, der Bedarf verlangt min=3. Die alte
    Erfindung `required_min = max(2, n_aktive // 8)` hätte hier 2 angesetzt
    und den Tag fälschlich als "ok" gewertet."""
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _QualDB())
    try:
        resp = client.get("/api/v1/quality-report?year=2026&month=1")
        assert resp.status_code == 200
        data = resp.json()
        day5 = next(d for d in data["coverage_days"] if d["day"] == 5)
        assert day5["required"] == 3
        assert day5["scheduled"] == 2
        assert day5["status"] == "critical"
        # Tage ohne definierten Bedarf: Werktage ohne Planung bleiben "unplanned"
        day6 = next(d for d in data["coverage_days"] if d["day"] == 6)
        assert day6["required"] is None
        assert day6["status"] == "unplanned"
        # Wochenende ohne Bedarf/Planung ist kein Befund
        day3 = next(d for d in data["coverage_days"] if d["day"] == 3)  # Sa
        assert day3["status"] == "ok"
    finally:
        _sessions.pop(tok, None)


def test_quality_report_invalid_month_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _QualDB())
    try:
        resp = client.get("/api/v1/quality-report?year=2026&month=0")
        assert resp.status_code == 400
    finally:
        _sessions.pop(tok, None)
