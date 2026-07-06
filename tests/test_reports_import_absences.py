"""Tests for the absence CSV import (POST /api/import/absences-csv) in reports.py
— per-row validation: missing fields, unknown personnel number, unknown
absence-type abbreviation, and invalid date are each skipped with an error,
while valid rows import. Driven with a fake db."""

import io
import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


class _ImportDB:
    def get_employees(self, include_hidden=False):
        return [{"ID": 1, "NUMBER": "100"}]

    def get_leave_types(self, include_hidden=False):
        return [{"ID": 1, "SHORTNAME": "U"}]

    def add_absence(self, employee_id, date, leave_type_id):
        return {"ID": 1}


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 992, "NAME": "imp_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_import_absences_csv_row_validation(monkeypatch):
    from sp5api.main import _sessions

    # Columns must upper-case to the recognised keys (NUMBER, DATE, SHORTNAME).
    csv_text = (
        "Number,Date,Shortname\n"
        "100,2026-07-15,U\n"  # valid → imported
        "999,2026-07-16,U\n"  # unknown personnel number → skip
        "100,2026-07-17,XX\n"  # unknown absence-type abbreviation → skip
        "100,not-a-date,U\n"  # invalid date → skip
        ",,\n"  # missing required fields → skip
    )
    client, tok = _admin_client(monkeypatch, _ImportDB())
    try:
        resp = client.post(
            "/api/v1/import/absences-csv",
            files={"file": ("abs.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 1
        assert data["skipped"] == 4
        assert len(data["errors"]) == 4
        # each skipped row reports its (1-based, header-offset) row number
        assert all("row" in e and "reason" in e for e in data["errors"])
    finally:
        _sessions.pop(tok, None)


def test_import_absences_csv_persists_correct_values(admin_client):
    """Der Fake-DB-Test oben prüft nur `imported==1` — er kann NICHT sehen, ob
    die Abwesenheit mit dem richtigen Mitarbeiter/Datum/Abwesenheitsart landet.
    Zahlungs-/urlaubsrelevant: gegen die echte Golden-DB importieren und per
    Readback bestätigen, dass emp_id, date UND leave_type_id exakt stimmen."""
    emps = admin_client.get("/api/employees").json()
    emp = next((e for e in emps if str(e.get("NUMBER", "")).strip()), emps[0])
    number = str(emp["NUMBER"]).strip()
    lt = admin_client.get("/api/leave-types").json()[0]
    short = lt["SHORTNAME"].strip()

    csv = f"Personalnummer,Datum,Abwesenheitsart\r\n{number},2033-05-10,{short}\r\n".encode()
    r = admin_client.post(
        "/api/import/absences-csv",
        files={"file": ("abs.csv", io.BytesIO(csv), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    rows = admin_client.get(f"/api/absences?employee_id={emp['ID']}").json()
    hit = [a for a in rows if a.get("date") == "2033-05-10"]
    assert len(hit) == 1, "importierte Abwesenheit nicht (korrekt) persistiert"
    assert hit[0]["employee_id"] == emp["ID"]
    assert hit[0]["leave_type_id"] == lt["ID"]
