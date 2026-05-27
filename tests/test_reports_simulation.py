"""Tests for the schedule simulation (POST /api/simulation) in reports.py —
"what if these employees drop out?" Per-day it compares baseline vs. simulated
staffing and classifies each day critical / degraded / ok. Driven with a fake db."""

import secrets

import api.routers.reports as reports
from starlette.testclient import TestClient


class _SimDB:
    def get_employees(self, include_hidden=False):
        return [
            {"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg", "SHORTNAME": "AB"},
            {"ID": 2, "FIRSTNAME": "Otto", "NAME": "Cole", "SHORTNAME": "OC"},
        ]

    def get_shifts(self, include_hidden=True):
        return [{"ID": 1, "NAME": "Früh", "SHORTNAME": "F"}]

    def get_schedule(self, year, month):
        return [
            # 01-05: only emp 1 → if absent → critical
            {"employee_id": 1, "date": "2026-01-05", "kind": "shift", "shift_id": 1},
            # 01-06: emp 1 + emp 2 → emp 1 absent → degraded (emp 2 covers)
            {"employee_id": 1, "date": "2026-01-06", "kind": "shift", "shift_id": 1},
            {"employee_id": 2, "date": "2026-01-06", "kind": "shift", "shift_id": 1},
            # 01-07: emp 2 only, nobody absent → ok
            {"employee_id": 2, "date": "2026-01-07", "kind": "shift", "shift_id": 1},
        ]


def _planer_client(monkeypatch, db):
    from api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 993,
        "NAME": "sim_planer",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_simulation_classifies_days(monkeypatch):
    from api.main import _sessions

    client, tok = _planer_client(monkeypatch, _SimDB())
    try:
        body = {
            "year": 2026,
            "month": 1,
            "scenario_name": "Anna fällt aus",
            "absences": [
                {"emp_id": 1, "dates": ["2026-01-05", "2026-01-06"]},
                {"emp_id": 99, "dates": "all"},  # exercises the "all" branch
            ],
        }
        resp = client.post("/api/v1/simulation", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenario_name"] == "Anna fällt aus"

        by_date = {d["date"]: d for d in data["days"]}
        assert by_date["2026-01-05"]["status"] == "critical"  # sole shift lost
        assert by_date["2026-01-06"]["status"] == "degraded"  # 1 of 2 lost
        assert by_date["2026-01-07"]["status"] == "ok"

        summary = data["summary"]
        assert summary["total_lost_shifts"] == 2
        assert summary["critical_days"] == 1
        assert summary["degraded_days"] == 1
    finally:
        _sessions.pop(tok, None)
