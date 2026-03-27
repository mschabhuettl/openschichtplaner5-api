"""Tests for Excel XLSX export endpoints (Q051)."""
import io
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import secrets

    from api.main import _sessions, app
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 999, "NAME": "test_planer_xlsx", "role": "Planer", "ADMIN": False, "RIGHTS": 2}
    c = TestClient(app)
    c.headers["X-Auth-Token"] = tok
    yield c
    _sessions.pop(tok, None)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

class TestScheduleXlsx:
    def test_schedule_xlsx_returns_xlsx(self, client):
        resp = client.get("/api/export/schedule?month=2026-03&format=xlsx")
        assert resp.status_code == 200
        assert XLSX_MIME in resp.headers.get("content-type", "")

    def test_schedule_xlsx_freeze_panes(self, client):
        resp = client.get("/api/export/schedule?month=2026-03&format=xlsx")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        assert ws.freeze_panes == "C2"
        assert ws.cell(1, 1).font.bold is True

class TestStatisticsXlsx:
    def test_statistics_xlsx_returns_xlsx(self, client):
        resp = client.get("/api/export/statistics?year=2026&format=xlsx")
        assert resp.status_code == 200
        assert XLSX_MIME in resp.headers.get("content-type", "")

    def test_statistics_xlsx_two_sheets(self, client):
        resp = client.get("/api/export/statistics?year=2026&format=xlsx")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        assert len(wb.sheetnames) == 2
        for ws in wb.worksheets:
            assert ws.freeze_panes == "A2"

class TestEmployeesXlsx:
    def test_employees_xlsx_returns_xlsx(self, client):
        resp = client.get("/api/export/employees?format=xlsx")
        assert resp.status_code == 200
        assert XLSX_MIME in resp.headers.get("content-type", "")

class TestAbsencesXlsx:
    def test_absences_xlsx_returns_xlsx(self, client):
        resp = client.get("/api/export/absences?year=2026&format=xlsx")
        assert resp.status_code == 200
        assert XLSX_MIME in resp.headers.get("content-type", "")

class TestXlsxConsistency:
    @pytest.mark.parametrize("url", [
        "/api/export/schedule?month=2026-03&format=xlsx",
        "/api/export/employees?format=xlsx",
        "/api/export/absences?year=2026&format=xlsx",
        "/api/export/statistics?year=2026&format=xlsx",
    ])
    def test_valid_xlsx(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 200
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        assert len(wb.sheetnames) >= 1
