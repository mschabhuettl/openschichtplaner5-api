"""P-VOLLERFASSUNG Lücke #4: Schichtmodell-Zuordnung über einen Zeitraum.
`POST /api/shift-cycles/assign` akzeptiert jetzt ein optionales `end_date`
(5CYASS.END, befristete Zuordnung) und reicht es an die lib weiter; ein Ende
vor dem Start wird mit 400 abgelehnt.
"""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.schedule as sched


class _CaptureDB:
    def __init__(self):
        self.captured = None

    def get_employee(self, _id):
        return {"ID": _id, "NAME": "X"}

    def get_shift_cycle(self, _id):
        return {"ID": _id, "NAME": "Zyklus"}

    def assign_cycle(self, employee_id, cycle_id, start_date, end_date=None):
        self.captured = {
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        return {
            "id": 1,
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "start": start_date,
            "end": end_date or "",
        }


def _write_session():
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 930, "NAME": "cy_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255,
    }
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(sched, "get_db", lambda: db)
    return TestClient(app, raise_server_exceptions=False)


def test_assign_cycle_forwards_end_date(monkeypatch):
    from sp5api.main import _sessions

    db = _CaptureDB()
    tok = _write_session()
    try:
        client = _client(monkeypatch, db)
        res = client.post(
            "/api/shift-cycles/assign",
            json={
                "employee_id": 10,
                "cycle_id": 1,
                "start_date": "2026-06-01",
                "end_date": "2026-09-30",
            },
            headers={"X-Auth-Token": tok},
        )
        assert res.status_code == 200, res.text
        assert db.captured["end_date"] == "2026-09-30"
    finally:
        _sessions.pop(tok, None)


def test_assign_cycle_without_end_is_open(monkeypatch):
    from sp5api.main import _sessions

    db = _CaptureDB()
    tok = _write_session()
    try:
        client = _client(monkeypatch, db)
        res = client.post(
            "/api/shift-cycles/assign",
            json={"employee_id": 10, "cycle_id": 1, "start_date": "2026-06-01"},
            headers={"X-Auth-Token": tok},
        )
        assert res.status_code == 200, res.text
        assert db.captured["end_date"] is None
    finally:
        _sessions.pop(tok, None)


def test_assign_cycle_end_before_start_rejected(monkeypatch):
    from sp5api.main import _sessions

    db = _CaptureDB()
    tok = _write_session()
    try:
        client = _client(monkeypatch, db)
        res = client.post(
            "/api/shift-cycles/assign",
            json={
                "employee_id": 10,
                "cycle_id": 1,
                "start_date": "2026-06-01",
                "end_date": "2026-05-01",
            },
            headers={"X-Auth-Token": tok},
        )
        assert res.status_code == 400
        assert db.captured is None  # lib gar nicht erreicht
    finally:
        _sessions.pop(tok, None)
