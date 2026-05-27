"""Tests for the monthly report export (GET /api/reports/monthly) in reports.py
— the month/format input guards and the no-data 404. Driven with a fake db."""

import secrets

import api.routers.reports as reports
from starlette.testclient import TestClient


class _EmptyStatsDB:
    def get_statistics(self, year, month, group_id=None):
        return []


def _admin_client(monkeypatch, db):
    from api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 995, "NAME": "mr_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


_BASE = "/api/v1/reports/monthly"


def test_invalid_month_returns_400(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        assert client.get(f"{_BASE}?year=2026&month=13").status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_invalid_format_returns_400(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        assert client.get(f"{_BASE}?year=2026&month=1&format=xml").status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_no_data_returns_404(monkeypatch):
    from api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        # valid params but the (fake) db has no statistics for the month
        assert client.get(f"{_BASE}?year=2026&month=1&format=csv").status_code == 404
    finally:
        _sessions.pop(tok, None)
