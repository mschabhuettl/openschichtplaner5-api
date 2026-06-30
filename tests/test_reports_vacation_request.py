"""Tests for the printable Urlaubsantrag form (GET /api/reports/vacation-request).

P2-6 (Punkt 15): the original's vacation approval is a printed form with
applicant/supervisor signature lines (SP5Res.dll: „Urlaubsantrag", „Genehmigt"/
„Abgelehnt", „Datum, Unterschrift Antragsteller/Vorgesetzter"). This endpoint
renders that form as a PDF. Driven with a fake db.
"""

import re
import secrets
import zlib

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


class _VacDB:
    def get_employee(self, eid):
        if eid != 1:
            return None
        return {"ID": 1, "NAME": "Müller", "FIRSTNAME": "Anna", "SHORTNAME": "AMÜ", "NUMBER": "4711"}

    def get_leave_type(self, ltid):
        return {"ID": ltid, "NAME": "Erholungsurlaub"}

    def get_absences_list(self, year=None, employee_id=None, leave_type_id=None):
        return [
            {"id": 10, "employee_id": 1, "date": "2027-05-03", "leave_type_id": 1,
             "leave_type_name": "Erholungsurlaub", "interval": 0, "start_time": 0, "end_time": 0},
            {"id": 11, "employee_id": 1, "date": "2027-05-04", "leave_type_id": 1,
             "leave_type_name": "Erholungsurlaub", "interval": 1, "start_time": 0, "end_time": 0},
        ]


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 995, "NAME": "mr_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


_BASE = "/api/v1/reports/vacation-request"


def _pdf_text(content: bytes) -> str:
    text = ""
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", content, re.S):
        try:
            text += zlib.decompress(m.group(1)).decode("latin-1", "replace")
        except Exception:
            pass
    return text


def test_vacation_request_pdf_contains_original_form_elements(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _VacDB())
    try:
        res = client.get(f"{_BASE}?employee_id=1&from_date=2027-05-03&to_date=2027-05-04&leave_type_id=1")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content[:4] == b"%PDF"
        text = _pdf_text(res.content)
        for needle in (
            "Urlaubsantrag",
            "Antragsteller",
            "Abwesenheitsart",
            "Erholungsurlaub",
            "Beantragter Urlaub",
            "Genehmigt",
            "Abgelehnt",
            "Unterschrift Antragsteller",
            "Unterschrift Vorgesetzter",
        ):
            assert needle in text, f"missing {needle!r} in PDF"
        # full day + half day → 1.5 days requested
        assert "1.5 Tage" in text
    finally:
        _sessions.pop(tok, None)


def test_vacation_request_unknown_employee_404(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _VacDB())
    try:
        res = client.get(f"{_BASE}?employee_id=999&from_date=2027-05-03&to_date=2027-05-04")
        assert res.status_code == 404
    finally:
        _sessions.pop(tok, None)


def test_vacation_request_bad_range_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _VacDB())
    try:
        res = client.get(f"{_BASE}?employee_id=1&from_date=2027-05-10&to_date=2027-05-01")
        assert res.status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_vacation_request_invalid_date_400(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _VacDB())
    try:
        res = client.get(f"{_BASE}?employee_id=1&from_date=2027-13-99&to_date=2027-05-01")
        assert res.status_code == 400
    finally:
        _sessions.pop(tok, None)


def test_requested_vacation_days_helper():
    assert reports._requested_vacation_days([]) == 0.0
    assert reports._requested_vacation_days([{"interval": 0}]) == 1.0
    assert reports._requested_vacation_days([{"interval": 1}, {"interval": 2}]) == 1.0  # 2× half
    assert reports._requested_vacation_days(
        [{"interval": 3, "start_time": 480, "end_time": 720}]  # 4h → 0.5 days
    ) == 0.5


def test_format_de_date_helper():
    assert reports._format_de_date("2027-05-03") == "03.05.2027"
    assert reports._format_de_date("not-a-date") == "not-a-date"
