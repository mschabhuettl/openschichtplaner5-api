"""Internals of the qualification-matrix router: the NOTE1 parser and the
stats-aggregation path. The bundled test DBF data has no employees carrying
parsed qualifications, so the aggregation loop is driven here with a fake db."""

import secrets

import api.routers.qualification_matrix as qm
from starlette.testclient import TestClient


class TestParseQualifications:
    def test_empty_inputs_return_empty_list(self):
        assert qm._parse_qualifications(None) == []
        assert qm._parse_qualifications("") == []
        assert qm._parse_qualifications("   ") == []

    def test_splits_on_comma_semicolon_slash_newline(self):
        assert qm._parse_qualifications("Ersthelfer, Stapler; Kran/Schweißen\nGabel") == [
            "Ersthelfer",
            "Stapler",
            "Kran",
            "Schweißen",
            "Gabel",
        ]


class _FakeDB:
    def __init__(self, employees):
        self._employees = employees

    def get_employees(self, include_hidden=False):
        return self._employees


def test_stats_aggregates_employee_qualifications(app, monkeypatch):
    """The stats endpoint counts qualifications and computes percentages."""
    employees = [
        {"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg", "NOTE1": "Ersthelfer, Stapler"},
        {"ID": 2, "FIRSTNAME": "", "NAME": "Cole", "NOTE1": "Stapler"},  # no firstname
        {"ID": 3, "FIRSTNAME": "Dora", "NAME": "Eich", "NOTE1": ""},  # no qualifications
    ]
    monkeypatch.setattr(qm, "get_db", lambda: _FakeDB(employees))

    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 904, "NAME": "qm_pl", "role": "Planer", "ADMIN": False, "RIGHTS": 1}
    try:
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        r = client.get("/api/v1/qualifications/stats")
        assert r.status_code == 200
        quals = {q["name"]: q for q in r.json()["qualifications"]}

        assert quals["Stapler"]["count"] == 2
        assert quals["Ersthelfer"]["count"] == 1
        # 2 of 3 employees → 66.7 %
        assert quals["Stapler"]["percentage"] == 66.7

        stapler_names = {e["name"] for e in quals["Stapler"]["employees"]}
        assert stapler_names == {"Anna Berg", "Cole"}  # empty firstname → surname only
    finally:
        _sessions.pop(tok, None)
