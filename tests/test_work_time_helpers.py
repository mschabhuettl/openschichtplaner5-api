"""Unit tests for the pure helpers in work_time_rules.

These functions take a `db` exposing `_read(table)` and parse DBF-shaped rows
into work-day / shift-time maps. The happy path runs through the endpoints;
here we drive the malformed-input, out-of-range and parse-fallback branches
directly with a fake db.
"""

from datetime import date

import api.routers.work_time_rules as wtr


class FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def _read(self, name):
        return self._tables.get(name, [])


def test_load_rules_corrupt_file_returns_defaults(tmp_path, monkeypatch):
    bad = tmp_path / "rules.json"
    bad.write_text("not json{", encoding="utf-8")
    monkeypatch.setattr(wtr, "_RULES_FILE", bad)
    assert wtr._load_rules() == dict(wtr._DEFAULT_RULES)


class TestShiftDuration:
    def test_none_shift_id_is_zero(self):
        assert wtr._get_shift_duration(FakeDB({}), None) == 0.0

    def test_matching_shift_returns_duration(self):
        db = FakeDB({"SHIFT": [{"ID": 1, "DURATION0": 8}]})
        assert wtr._get_shift_duration(db, 1) == 8.0

    def test_unknown_shift_returns_zero(self):
        db = FakeDB({"SHIFT": [{"ID": 1, "DURATION0": 8}]})
        assert wtr._get_shift_duration(db, 999) == 0.0


def test_collect_work_days_skips_bad_rows():
    db = FakeDB(
        {
            "SHIFT": [{"ID": 1, "DURATION0": 8}],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-02", "SHIFTID": 1},  # valid → 8h
                {"EMPLOYEEID": 5, "DATE": "2026-02-30", "SHIFTID": 1},  # invalid date → skip
                {"EMPLOYEEID": 9, "DATE": "2026-03-02", "SHIFTID": 1},  # other employee → skip
            ],
            "SPSHI": [
                {"EMPLOYEEID": 9, "DATE": "2026-03-03", "DURATION": 4},  # other employee → skip
                {"EMPLOYEEID": 5, "DATE": "", "DURATION": 4},  # empty date → skip
                {"EMPLOYEEID": 5, "DATE": "2026-02-30", "DURATION": 4},  # invalid date → skip
                {"EMPLOYEEID": 5, "DATE": "2026-03-04", "DURATION": 5},  # valid → 5h
            ],
        }
    )
    days = wtr._collect_work_days(db, 5, date(2026, 1, 1), date(2026, 12, 31))
    assert days == {date(2026, 3, 2): 8.0, date(2026, 3, 4): 5.0}


def test_collect_shift_times_parses_and_skips():
    db = FakeDB(
        {
            "SHIFT": [
                {"ID": 1, "STARTTIME": "08:00", "DURATION0": 8},  # HH:MM → 8.0
                {"ID": 2, "STARTTIME": "bad:mm", "DURATION": 4},  # bad HH:MM → default 8.0
                {"ID": 3, "STARTTIME": "9.5", "DURATION0": 2},  # decimal → 9.5
                {"ID": 4, "STARTTIME": "xyz", "DURATION0": 2},  # non-numeric → default 8.0
                {"ID": 5, "DURATION0": 2},  # no start time → default 8.0
            ],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-01", "SHIFTID": 1},
                {"EMPLOYEEID": 5, "DATE": "2026-03-02", "SHIFTID": 2},
                {"EMPLOYEEID": 5, "DATE": "2026-03-03", "SHIFTID": 3},
                {"EMPLOYEEID": 5, "DATE": "2026-03-04", "SHIFTID": 4},
                {"EMPLOYEEID": 5, "DATE": "2026-03-05", "SHIFTID": 5},
                {"EMPLOYEEID": 5, "DATE": "2026-02-30", "SHIFTID": 1},  # invalid date → skip
            ],
            "SPSHI": [
                {"EMPLOYEEID": 9, "DATE": "2026-03-06", "DURATION": 4},  # other employee → skip
                {"EMPLOYEEID": 5, "DATE": "", "DURATION": 4},  # empty date → skip
                {"EMPLOYEEID": 5, "DATE": "2026-02-30", "DURATION": 4},  # invalid date → skip
                {"EMPLOYEEID": 5, "DATE": "2026-03-07", "DURATION": 6},  # valid
            ],
        }
    )
    entries = wtr._collect_shift_times(db, 5, date(2026, 1, 1), date(2026, 12, 31))
    starts = {e["date"]: e["start_hour"] for e in entries}
    assert len(entries) == 6  # 5 valid MASHI + 1 valid SPSHI
    assert starts[date(2026, 3, 1)] == 8.0  # "08:00"
    assert starts[date(2026, 3, 2)] == 8.0  # bad HH:MM → default
    assert starts[date(2026, 3, 3)] == 9.5  # decimal
    assert starts[date(2026, 3, 4)] == 8.0  # non-numeric → default
    assert starts[date(2026, 3, 5)] == 8.0  # missing → default


def test_check_employee_resets_consecutive_run_on_gap():
    """Non-adjacent working days reset the consecutive-day counter."""
    db = FakeDB(
        {
            "SHIFT": [{"ID": 1, "DURATION0": 8}],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-01", "SHIFTID": 1},
                {"EMPLOYEEID": 5, "DATE": "2026-03-03", "SHIFTID": 1},  # gap → counter resets
            ],
            "SPSHI": [],
        }
    )
    violations = wtr._check_employee(
        db, 5, date(2026, 1, 1), date(2026, 12, 31), dict(wtr._DEFAULT_RULES)
    )
    # 8h/day < default 10h max and no consecutive run → no violations
    assert violations == []


def test_check_all_skips_employee_without_id(app, monkeypatch):
    """check-all tolerates an employee row that has no ID."""
    import secrets

    from api.main import _sessions
    from starlette.testclient import TestClient

    class DB2(FakeDB):
        def get_employees(self, include_hidden=False):
            return [{"ID": None, "GROUPID": 1}, {"ID": 5, "GROUPID": 1}]

        def get_employee(self, eid):
            return {"ID": eid}

    monkeypatch.setattr(wtr, "get_db", lambda: DB2({"SHIFT": [], "MASHI": [], "SPSHI": []}))

    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 903, "NAME": "wt_pl", "role": "Planer", "ADMIN": False, "RIGHTS": 1}
    try:
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["X-Auth-Token"] = tok
        res = client.post("/api/v1/work-time-rules/check-all?from=2026-01-01&to=2026-12-31")
        assert res.status_code == 200
        assert "violations" in res.json()
    finally:
        _sessions.pop(tok, None)
