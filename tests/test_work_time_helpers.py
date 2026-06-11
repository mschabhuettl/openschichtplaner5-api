"""Unit tests for the work_time_rules data layer (Befund D7).

The ArbZG checks themselves are an api extension, but their data base must
follow spec 3.4: hours per day index DURATION[Ft?7:wd] (3.4.3 no. 5/6),
5SPSHI with SHIFTID replaces the normal duty (3.4.4 no. 12), cycle-planned
employees via 5CYASS expansion (3.4.2), and shift times from
STARTEND[Ft?7:wd] instead of the nonexistent STARTTIME/START fields.
"""

from datetime import date, datetime

import sp5api.routers.work_time_rules as wtr


class FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def _read(self, name):
        return self._tables.get(name, [])


def _shift(sid: int, start: str, end: str, hours: float, days=range(7)) -> dict:
    """Spec-shaped 5SHIFT record: same window/hours on the given day indexes."""
    rec: dict = {"ID": sid}
    for i in days:
        rec[f"STARTEND{i}"] = f"{start}-{end}"
        rec[f"DURATION{i}"] = hours
    return rec


_RULES = dict(wtr._DEFAULT_RULES)


def test_load_rules_corrupt_file_returns_defaults(tmp_path, monkeypatch):
    bad = tmp_path / "rules.json"
    bad.write_text("not json{", encoding="utf-8")
    monkeypatch.setattr(wtr, "_RULES_FILE", bad)
    assert wtr._load_rules() == dict(wtr._DEFAULT_RULES)


def test_collect_day_data_skips_bad_rows():
    db = FakeDB(
        {
            "SHIFT": [_shift(1, "08:00", "16:00", 8.0)],
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
    day_hours, _blocks = wtr._collect_day_data(db, 5, date(2026, 1, 1), date(2026, 12, 31))
    assert day_hours == {date(2026, 3, 2): 8.0, date(2026, 3, 4): 5.0}


def test_hours_use_day_index_not_duration0():
    """Sa-Dienst zählt DURATION[5], nicht pauschal DURATION0 (3.4.3 Nr. 5/6)."""
    db = FakeDB(
        {
            # Mo-Fr 8h, Sa/So keine Zeiten definiert (Slot leer, DURATION 0)
            "SHIFT": [_shift(1, "08:00", "16:00", 8.0, days=range(5))],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-02", "SHIFTID": 1},  # Mo → 8h
                {"EMPLOYEEID": 5, "DATE": "2026-03-07", "SHIFTID": 1},  # Sa → 0h
            ],
        }
    )
    day_hours, _ = wtr._collect_day_data(db, 5, date(2026, 3, 1), date(2026, 3, 31))
    assert day_hours[date(2026, 3, 2)] == 8.0
    assert day_hours[date(2026, 3, 7)] == 0.0


def test_holiday_uses_index7():
    """Feiertag nutzt STARTEND7/DURATION7 (D-34), nicht den Wochentagsslot."""
    shift = _shift(1, "08:00", "16:00", 8.0)
    shift["STARTEND7"] = "10:00-14:00"
    shift["DURATION7"] = 4.0
    db = FakeDB(
        {
            "SHIFT": [shift],
            "HOLID": [{"DATE": "2026-03-04", "INTERVAL": 0}],  # Mittwoch = Feiertag
            "MASHI": [{"EMPLOYEEID": 5, "DATE": "2026-03-04", "SHIFTID": 1}],
        }
    )
    day_hours, blocks = wtr._collect_day_data(db, 5, date(2026, 3, 1), date(2026, 3, 31))
    assert day_hours[date(2026, 3, 4)] == 4.0
    assert blocks[0]["start"] == datetime(2026, 3, 4, 10, 0)
    assert blocks[0]["end"] == datetime(2026, 3, 4, 14, 0)


def test_spshi_replaces_normal_duty_no_double_counting():
    """5SPSHI mit SHIFTID ersetzt den Normaldienst (3.4.4 Nr. 12) — keine Addition."""
    db = FakeDB(
        {
            "SHIFT": [_shift(1, "08:00", "16:00", 8.0)],
            "MASHI": [{"EMPLOYEEID": 5, "DATE": "2026-03-02", "SHIFTID": 1}],
            "SPSHI": [
                {
                    "EMPLOYEEID": 5,
                    "DATE": "2026-03-02",
                    "SHIFTID": 1,
                    "DURATION": 6.0,
                    "STARTEND": "08:00-14:00",
                }
            ],
        }
    )
    day_hours, _ = wtr._collect_day_data(db, 5, date(2026, 3, 1), date(2026, 3, 31))
    assert day_hours == {date(2026, 3, 2): 6.0}

    # Keine Schein-Verletzung von max 10h/Tag durch 8+6=14h-Addition
    violations = wtr._check_employee(db, 5, date(2026, 3, 1), date(2026, 3, 31), _RULES)
    assert [v for v in violations if v["type"] == "max_hours_per_day"] == []


def test_cycle_planned_employees_are_checked():
    """Zyklusgeplante MA (5CYASS) sind nicht mehr unsichtbar (3.4.2)."""
    db = FakeDB(
        {
            "SHIFT": [_shift(10, "06:00", "18:00", 12.0)],
            "CYCLE": [{"ID": 1, "SIZE": 7, "UNIT": 0}],  # Tagesmodell, 7 Positionen
            "CYENT": [
                {"CYCLEEID": 1, "INDEX": i, "SHIFTID": 10, "WORKPLACID": 0}
                for i in range(7)
            ],
            "CYASS": [
                {"ID": 1, "EMPLOYEEID": 5, "CYCLEID": 1, "START": "2026-03-02", "ENTRANCE": 0}
            ],
        }
    )
    day_hours, _ = wtr._collect_day_data(db, 5, date(2026, 3, 2), date(2026, 3, 8))
    assert len(day_hours) == 7
    violations = wtr._check_employee(db, 5, date(2026, 3, 2), date(2026, 3, 8), _RULES)
    types = {v["type"] for v in violations}
    assert "max_hours_per_day" in types
    assert "max_consecutive_days" in types


def test_rest_time_uses_real_startend_times():
    """Ruhezeit aus echten STARTEND-Zeiten statt geratenem 8:00-Start.

    Nachtdienst 22-06 gefolgt von Tagdienst ab 08:00 ⇒ 2h Ruhe < 11h.
    """
    db = FakeDB(
        {
            "SHIFT": [
                _shift(20, "22:00", "06:00", 8.0),
                _shift(21, "08:00", "16:00", 8.0),
            ],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-02", "SHIFTID": 20},
                {"EMPLOYEEID": 5, "DATE": "2026-03-03", "SHIFTID": 21},
            ],
        }
    )
    _, blocks = wtr._collect_day_data(db, 5, date(2026, 3, 1), date(2026, 3, 31))
    # Tageswechsel-Konvention D-30: Ende <= Start ⇒ +24h
    assert blocks[0]["end"] == datetime(2026, 3, 3, 6, 0)

    violations = wtr._check_employee(db, 5, date(2026, 3, 1), date(2026, 3, 31), _RULES)
    rest = [v for v in violations if v["type"] == "min_rest_hours_between_shifts"]
    assert len(rest) == 1
    assert rest[0]["value"] == 2.0


def test_duty_without_times_yields_no_block():
    """Leerer STARTEND-Slot = keine Zeiten definiert — kein erfundener Block."""
    db = FakeDB(
        {
            "SHIFT": [_shift(1, "08:00", "16:00", 8.0, days=range(5))],
            "MASHI": [{"EMPLOYEEID": 5, "DATE": "2026-03-07", "SHIFTID": 1}],  # Sa
            "SPSHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-08", "DURATION": 4.0, "STARTEND": ""}
            ],
        }
    )
    _, blocks = wtr._collect_day_data(db, 5, date(2026, 3, 1), date(2026, 3, 31))
    assert blocks == []


def test_check_employee_resets_consecutive_run_on_gap():
    """Non-adjacent working days reset the consecutive-day counter."""
    db = FakeDB(
        {
            "SHIFT": [_shift(1, "08:00", "16:00", 8.0)],
            "MASHI": [
                {"EMPLOYEEID": 5, "DATE": "2026-03-01", "SHIFTID": 1},
                {"EMPLOYEEID": 5, "DATE": "2026-03-03", "SHIFTID": 1},  # gap → counter resets
            ],
            "SPSHI": [],
        }
    )
    violations = wtr._check_employee(db, 5, date(2026, 1, 1), date(2026, 12, 31), _RULES)
    # 8h/day < default 10h max and no consecutive run → no violations
    assert violations == []


def test_check_all_skips_employee_without_id(app, monkeypatch):
    """check-all tolerates an employee row that has no ID."""
    import secrets

    from starlette.testclient import TestClient

    from sp5api.main import _sessions

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
