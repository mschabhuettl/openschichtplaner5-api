"""Tests for the fairness-score analytics (GET /api/fairness) in reports.py.

Befund D10: gezählt werden Diensttage (Spec 3.4.5 Nr. 14/15) — je Tag mit
mindestens einem Dienst — mit den Original-Zählern Sonntag/Feiertag (inkl.
So-und-Ft-Doppelzählung); geplante Dienste an Abwesenheitstagen zählen mit
(die Absence-Unterdrückung war eine Erfindung). Wochenend-/Nacht-Zähler
bleiben als gekennzeichnete api-Erweiterung. A fake db drives the
computation."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.reports as reports


def _admin_client(monkeypatch, db):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(reports, "get_db", lambda: db)
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 970,
        "NAME": "fair_admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
    }
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


class _FairDB:
    def get_employees(self, include_hidden=False):
        return [
            {"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg", "SHORTNAME": "AB"},
            {"ID": 2, "FIRSTNAME": "Otto", "NAME": "Cole", "SHORTNAME": "OC"},
        ]

    def get_group_members(self, gid):
        return [1, 2]

    def get_shifts(self):
        return [
            {"ID": 1, "STARTEND0": "08:00-16:00"},  # day shift
            {"ID": 2, "STARTEND0": "22:00-06:00"},  # night shift (start >= 20)
        ]

    def get_holidays(self, year):
        # Neujahr (Do) + ein Feiertag auf einem Sonntag (So-und-Ft-Doppelzählung)
        return [{"DATE": "2026-01-01"}, {"DATE": "2026-01-04"}]

    def get_schedule(self, year, month, group_id=None):
        if month != 1:
            return []
        return [
            # emp 1: weekend + night shift (2026-01-03 is a Saturday)
            {"employee_id": 1, "kind": "shift", "date": "2026-01-03", "shift_id": 2},
            # emp 1: holiday shift (2026-01-01)
            {"employee_id": 1, "kind": "shift", "date": "2026-01-01", "shift_id": 1},
            # emp 1: absent on 2026-01-05 — der geplante Dienst zählt TROTZDEM
            # (Spec 3.4: keine Absence-Unterdrückung; Befund D10)
            {"employee_id": 1, "kind": "absence", "date": "2026-01-05"},
            {"employee_id": 1, "kind": "shift", "date": "2026-01-05", "shift_id": 1},
            # emp 1: zwei Dienste am selben Tag = EIN Diensttag (3.4.5)
            {"employee_id": 1, "kind": "shift", "date": "2026-01-06", "shift_id": 1},
            {"employee_id": 1, "kind": "special_shift", "date": "2026-01-06", "shift_id": 0},
            # emp 2: one ordinary weekday shift (2026-01-06 is a Tuesday)
            {"employee_id": 2, "kind": "shift", "date": "2026-01-06", "shift_id": 1},
            # emp 2: Dienst am Sonntag, der zugleich Feiertag ist (2026-01-04)
            # → zählt in Sonntag UND Feiertag (3.4.5 Nr. 15)
            {"employee_id": 2, "kind": "shift", "date": "2026-01-04", "shift_id": 1},
        ]


def test_fairness_score_counts_and_scores(monkeypatch):
    from sp5api.main import _sessions

    client, tok = _admin_client(monkeypatch, _FairDB())
    try:
        resp = client.get("/api/v1/fairness?year=2026")
        assert resp.status_code == 200
        data = resp.json()

        by_id = {e["employee_id"]: e for e in data["employees"]}
        assert set(by_id) == {1, 2}
        # emp 1: 4 Diensttage (3./1./5./6. — der 6. hat zwei Dienste, zählt
        # einmal; der Abwesenheitstag 5. zählt mit), 1 Wochenende, 1 Nacht,
        # 1 Feiertag, 0 Sonntage
        assert by_id[1]["total"] == 4
        assert by_id[1]["weekend"] == 1
        assert by_id[1]["night"] == 1
        assert by_id[1]["holiday"] == 1
        assert by_id[1]["sunday"] == 0
        # emp 2: 2 Diensttage; der 4.1. ist Sonntag UND Feiertag → beide Zähler
        assert by_id[2]["total"] == 2
        assert by_id[2]["sunday"] == 1
        assert by_id[2]["holiday"] == 1
        assert by_id[2]["weekend"] == 1

        fairness = data["fairness"]
        for key in ("weekend_score", "night_score", "holiday_score", "total_score", "overall"):
            assert key in fairness
            assert 0 <= fairness[key] <= 100
    finally:
        _sessions.pop(tok, None)


def test_fairness_empty_when_no_shifts(monkeypatch):
    from sp5api.main import _sessions

    class _EmptyDB(_FairDB):
        def get_schedule(self, year, month, group_id=None):
            return []

    client, tok = _admin_client(monkeypatch, _EmptyDB())
    try:
        resp = client.get("/api/v1/fairness?year=2026")
        assert resp.status_code == 200
        assert resp.json()["employees"] == []
    finally:
        _sessions.pop(tok, None)
