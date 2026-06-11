"""Befund D10 (/api/statistics/shifts): Die Früh/Spät/Nacht-Kategorisierung
ist eine gekennzeichnete api-Erweiterung; die Startstunde muss aus dem echten
STARTEND0-Fenster kommen — das zuvor gelesene Feld FROM0 existiert in 5SHIFT
nicht (die Stunden-Heuristik war faktisch tot, es griff nur Namens-Matching).
"""

import secrets
from datetime import date

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


class _StatDB:
    """Fake db: vier Schichten ohne sprechende Namen — Kategorie darf nur über
    die STARTEND0-Startstunde kommen."""

    def get_shifts(self, include_hidden=True):
        return [
            {"ID": 1, "NAME": "A", "SHORTNAME": "A1", "STARTEND0": "06:00-14:00"},
            {"ID": 2, "NAME": "B", "SHORTNAME": "B1", "STARTEND0": "14:00-22:00"},
            {"ID": 3, "NAME": "C", "SHORTNAME": "C1", "STARTEND0": "22:00-06:00"},
            {"ID": 4, "NAME": "D", "SHORTNAME": "D1", "STARTEND0": ""},
        ]

    def get_employees(self, include_hidden=False):
        return [{"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg", "SHORTNAME": "AB"}]

    def get_schedule(self, year, month, group_id=None):
        today = date.today()
        if (year, month) != (today.year, today.month):
            return []
        d = today.isoformat()
        return [
            {"employee_id": 1, "kind": "shift", "date": d, "shift_id": sid}
            for sid in (1, 2, 3, 4)
        ]


def test_categories_come_from_startend0(monkeypatch):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: _StatDB())
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 971, "NAME": "stat_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    try:
        resp = client.get("/api/statistics/shifts?year=2026&months=1")
        assert resp.status_code == 200
        data = resp.json()
        cat_by_id = {u["shift_id"]: u["category"] for u in data["shift_usage"]}
        assert cat_by_id[1] == "Früh"
        assert cat_by_id[2] == "Spät"
        assert cat_by_id[3] == "Nacht"
        # ohne Zeiten und ohne sprechenden Namen → Sonstige
        assert cat_by_id[4] == "Sonstige"
    finally:
        _sessions.pop(tok, None)
