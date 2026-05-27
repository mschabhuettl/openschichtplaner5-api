"""Tests for the monthly quality report (GET /api/quality-report) in reports.py
— per-day staffing coverage, hours-compliance issues, the weighted quality
score/grade and the findings list. Driven with a fake db."""

import secrets

import api.routers.reports as reports
from starlette.testclient import TestClient

_PREFIX = "2026-01"


class _QualDB:
    def _read(self, name):
        if name == "EMPL":
            return [{"ID": 1, "HIDE": 0}, {"ID": 2, "HIDE": 0}]
        if name == "SHIFT":
            return [{"ID": 1, "DURATION": 480}]  # 8h (minutes → /60)
        if name == "MASHI":
            # both employees scheduled on a Monday (2026-01-05) → an "ok" day
            return [
                {"DATE": "2026-01-05", "EMPLOYEEID": 1, "SHIFTID": 1},
                {"DATE": "2026-01-05", "EMPLOYEEID": 2, "SHIFTID": 1},
            ]
        if name == "ABSEN":
            return [{"DATE": "2026-01-06", "EMPLOYEEID": 1, "LEAVETYPEID": 1}]
        if name == "LEAVETYP":
            return [{"ID": 1, "NAME": "Urlaub"}]
        return []

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
    from api.main import _sessions, app

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
    from api.main import _sessions

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


def test_quality_report_invalid_month_400(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _QualDB())
    try:
        resp = client.get("/api/v1/quality-report?year=2026&month=0")
        assert resp.status_code == 400
    finally:
        _sessions.pop(tok, None)
