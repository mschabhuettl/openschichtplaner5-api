"""Unit tests for the conflict-report core: time-parsing helpers and the
_detect_conflicts engine. Driven directly with a fake db so the
double-booked / overlap / no-time branches and the SPSHI path are exercised
without seeding DBF fixtures."""

import sys
from datetime import date

import api.routers.conflict_report as cr


class TestTimeHelpers:
    def test_parse_time_str(self):
        assert cr._parse_time_str("06:30") == 390
        assert cr._parse_time_str("") is None
        assert cr._parse_time_str("not-a-time") is None

    def test_parse_startend(self):
        assert cr._parse_startend("06:00-14:00") == (360, 840)
        assert cr._parse_startend("no-dash-here") is None
        assert cr._parse_startend("06:00-bad") is None
        # overnight wraps past midnight
        assert cr._parse_startend("22:00-06:00") == (1320, 1800)

    def test_shift_time_range(self):
        # weekday-specific key wins
        shift = {"STARTEND0": "06:00-14:00", "STARTEND2": "13:00-21:00"}
        assert cr._shift_time_range(shift, 2) == (780, 1260)
        # falls back to STARTEND0 when the weekday key is empty
        assert cr._shift_time_range({"STARTEND0": "06:00-14:00"}, 3) == (360, 840)
        # no usable time data → None
        assert cr._shift_time_range({}, 0) is None


class _FakeDB:
    def __init__(self, tables, employees, shifts, groups, members):
        self._tables = tables
        self._employees = employees
        self._shifts = shifts
        self._groups = groups
        self._members = members  # {group_id: [emp_id,...]}

    def _read(self, name):
        return self._tables.get(name, [])

    def get_employees(self, include_hidden=False):
        return self._employees

    def get_shifts(self, include_hidden=True):
        return self._shifts

    def get_groups(self):
        return self._groups

    def get_group_members(self, gid):
        return self._members.get(gid, [])


def _detect_db(spshi=None):
    employees = [{"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg"}]
    shifts = [
        {"ID": 1, "NAME": "Früh", "STARTEND0": "06:00-14:00"},
        {"ID": 2, "NAME": "Früh-Dup", "STARTEND0": "06:00-14:00"},  # identical → double
        {"ID": 3, "NAME": "Spät", "STARTEND0": "13:00-21:00"},  # overlaps Früh
        {"ID": 4, "NAME": "Vag-A", "STARTEND0": ""},  # no time
        {"ID": 5, "NAME": "Vag-B", "STARTEND0": ""},  # no time
    ]
    mashi = [
        # 2026-03-02: identical times → double_booked
        {"EMPLOYEEID": 1, "DATE": "2026-03-02", "SHIFTID": 1},
        {"EMPLOYEEID": 1, "DATE": "2026-03-02", "SHIFTID": 2},
        # 2026-03-03: overlapping times → overlap warning
        {"EMPLOYEEID": 1, "DATE": "2026-03-03", "SHIFTID": 1},
        {"EMPLOYEEID": 1, "DATE": "2026-03-03", "SHIFTID": 3},
        # 2026-03-04: no time data → overlap warning (no-time branch)
        {"EMPLOYEEID": 1, "DATE": "2026-03-04", "SHIFTID": 4},
        {"EMPLOYEEID": 1, "DATE": "2026-03-04", "SHIFTID": 5},
    ]
    return _FakeDB(
        {"MASHI": mashi, "SPSHI": spshi or []},
        employees,
        shifts,
        [{"ID": 10, "NAME": "Team"}],
        {10: [1]},
    )


class TestDetectConflicts:
    def test_double_overlap_and_notime(self):
        conflicts = cr._detect_conflicts(_detect_db(), date(2026, 3, 1), date(2026, 3, 31), None)
        by_date = {(c["date"], c["type"]) for c in conflicts}
        assert ("2026-03-02", "double_booked") in by_date
        assert ("2026-03-03", "overlap") in by_date
        assert ("2026-03-04", "overlap") in by_date

        # the no-time overlap uses the "multiple shifts" wording
        notime = [c for c in conflicts if c["date"] == "2026-03-04"][0]
        assert "multiple shifts" in notime["description"]

        # group_id is auto-resolved from membership when not passed
        double = [c for c in conflicts if c["type"] == "double_booked"][0]
        assert double["group_id"] == 10
        assert double["severity"] == "error"

    def test_spshi_special_shift_is_collected(self):
        # A SPSHI shift (TYPE 0) on a day that already has a MASHI shift with the
        # same time creates a double-booking; TYPE!=0 and unknown employees are skipped.
        spshi = [
            {"EMPLOYEEID": 1, "DATE": "2026-03-02", "SHIFTID": 3, "TYPE": 0},
            {"EMPLOYEEID": 1, "DATE": "2026-03-02", "SHIFTID": 1, "TYPE": 1},  # absence → skip
            {"EMPLOYEEID": 999, "DATE": "2026-03-02", "SHIFTID": 1, "TYPE": 0},  # unknown → skip
            {"EMPLOYEEID": 1, "DATE": "", "SHIFTID": 1, "TYPE": 0},  # blank date → skip
        ]
        conflicts = cr._detect_conflicts(
            _detect_db(spshi), date(2026, 3, 1), date(2026, 3, 31), None
        )
        # 2026-03-02 now has shifts 1, 2 (MASHI) + 3 (SPSHI) → at least one conflict
        assert any(c["date"] == "2026-03-02" for c in conflicts)

    def test_group_filter_and_invalid_dates(self):
        """Group filtering drops non-members; unknown employees and
        calendar-invalid dates are handled without error."""
        employees = [
            {"ID": 1, "FIRSTNAME": "Anna", "NAME": "Berg"},
            {"ID": 2, "FIRSTNAME": "Otto", "NAME": "Cole"},  # not in group 10
        ]
        shifts = [
            {"ID": 1, "NAME": "Früh", "STARTEND0": "06:00-14:00"},
            {"ID": 2, "NAME": "Früh-Dup", "STARTEND0": "06:00-14:00"},
        ]
        mashi = [
            {"EMPLOYEEID": 999, "DATE": "2026-02-10", "SHIFTID": 1},  # unknown emp → skip
            {"EMPLOYEEID": 2, "DATE": "2026-02-10", "SHIFTID": 1},  # non-member → skip
            {"EMPLOYEEID": 2, "DATE": "2026-02-10", "SHIFTID": 2},
            {"EMPLOYEEID": 1, "DATE": "2026-02-30", "SHIFTID": 1},  # invalid date → wd fallback
            {"EMPLOYEEID": 1, "DATE": "2026-02-30", "SHIFTID": 2},
        ]
        spshi = [
            {"EMPLOYEEID": 2, "DATE": "2026-02-10", "SHIFTID": 1, "TYPE": 0},  # non-member → skip
        ]
        db = _FakeDB(
            {"MASHI": mashi, "SPSHI": spshi},
            employees,
            shifts,
            [{"ID": 10, "NAME": "Team"}],
            {10: [1]},
        )
        conflicts = cr._detect_conflicts(db, date(2026, 2, 1), date(2026, 3, 31), group_id=10)
        # Only employee 1 (the member) yields a conflict, on the invalid date
        assert all(c["employee_id"] == 1 for c in conflicts if c["type"] != "understaffed")
        assert any(c["date"] == "2026-02-30" and c["type"] == "double_booked" for c in conflicts)

    def test_tolerates_spshi_read_error(self):
        class _BoomSPSHI(_FakeDB):
            def _read(self, name):
                if name == "SPSHI":
                    raise RuntimeError("SPSHI read failed")
                return self._tables.get(name, [])

        db = _BoomSPSHI(
            {"MASHI": []},
            [{"ID": 1, "FIRSTNAME": "A", "NAME": "B"}],
            [],
            [{"ID": 10}],
            {10: [1]},
        )
        # Must not raise despite the SPSHI read blowing up.
        assert isinstance(cr._detect_conflicts(db, date(2026, 3, 1), date(2026, 3, 2), None), list)


class TestExportValidation:
    """Input-validation guards on the CSV/XLSX export endpoint."""

    _URL = "/api/v1/reports/conflicts/export"

    def test_invalid_date_format_returns_400(self, write_client):
        resp = write_client.get(self._URL, params={"from": "not-a-date", "to": "2026-01-31"})
        assert resp.status_code == 400

    def test_to_before_from_returns_400(self, write_client):
        resp = write_client.get(self._URL, params={"from": "2026-06-01", "to": "2026-01-01"})
        assert resp.status_code == 400

    def test_range_over_366_days_returns_400(self, write_client):
        resp = write_client.get(self._URL, params={"from": "2025-01-01", "to": "2026-12-31"})
        assert resp.status_code == 400

    def test_xlsx_without_openpyxl_returns_500(self, write_client, monkeypatch):
        # Make `import openpyxl` fail inside the endpoint.
        monkeypatch.setitem(sys.modules, "openpyxl", None)
        resp = write_client.get(
            self._URL,
            params={"from": "2026-01-01", "to": "2026-01-31", "format": "xlsx"},
        )
        assert resp.status_code == 500
