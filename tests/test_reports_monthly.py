"""Tests for the monthly report export (GET /api/reports/monthly) in reports.py
— the month/format input guards and the no-data 404. Driven with a fake db."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


class _EmptyStatsDB:
    def get_statistics(self, year, month, group_id=None):
        return []


class _OneEmpStatsDB:
    def get_statistics(self, year, month, group_id=None):
        return [
            {
                "employee_id": 1,
                "employee_name": "Müller, Anna",
                "employee_short": "MA",
                "group_name": "Station 1",
                "target_hours": 160.0,
                "actual_hours": 158.0,
                "overtime_hours": -2.0,
                "shifts_count": 20,
                "absence_days": 1,
                "vacation_used": 1,
                "sick_days": 0,
            }
        ]

    def get_extracharges(self, include_hidden=False):
        return []


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 995, "NAME": "mr_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


_BASE = "/api/v1/reports/monthly"


def test_invalid_month_returns_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        assert client.get(f"{_BASE}?year=2026&month=13").status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_invalid_format_returns_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        assert client.get(f"{_BASE}?year=2026&month=1&format=xml").status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_no_data_returns_404(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _EmptyStatsDB())
    try:
        # valid params but the (fake) db has no statistics for the month
        assert client.get(f"{_BASE}?year=2026&month=1&format=csv").status_code == 404
    finally:
        _sessions.pop(tok, None)


# ── P-VOLLERFASSUNG Lücke #23: eigener Berichtstitel + Fußtext (PDF) ──────────


def test_report_title_helper_uses_custom_or_default():
    assert reports._monthly_report_title("Quartalsbericht Q2") == "Quartalsbericht Q2"
    assert reports._monthly_report_title(None) == "Monatsabschluss-Report"
    assert reports._monthly_report_title("   ") == "Monatsabschluss-Report"
    # nicht-Latin-1 (Emoji) wird ersetzt statt zu crashen
    assert "?" in reports._monthly_report_title("Bericht 🚀")


def test_report_footer_helper_uses_custom_or_default():
    # Latin-1-sicherer Text (inkl. Umlaut) bleibt unverändert
    assert reports._monthly_report_footer("Vertraulich - Klinik Süd") == "Vertraulich - Klinik Süd"
    assert reports._monthly_report_footer(None) is None
    assert reports._monthly_report_footer("  ") is None
    # nicht-Latin-1 (En-Dash) wird ersetzt statt zu crashen
    assert reports._monthly_report_footer("a – b") == "a ? b"


def test_pdf_with_custom_title_and_footer_returns_200(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _OneEmpStatsDB())
    try:
        res = client.get(
            f"{_BASE}?year=2026&month=1&format=pdf"
            "&title=Quartalsbericht%20Q2&footer=Vertraulich"
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"
    finally:
        _sessions.pop(tok, None)


def test_pdf_with_non_latin1_title_does_not_500(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _OneEmpStatsDB())
    try:
        res = client.get(f"{_BASE}?year=2026&month=1&format=pdf&title=Bericht%20%F0%9F%9A%80")
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"
    finally:
        _sessions.pop(tok, None)
