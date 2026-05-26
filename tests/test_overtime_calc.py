"""Unit tests for the _calc_overtime helper in api.routers.overtime.

Drives the MASHI/SPSHI hour-summation branches directly via a stub db (the helper
calls get_db() internally), covering the shift-duration lookup, the unknown-shift
fallback, the special-shift path, and the other-employee skip.
"""

import pytest
from api.routers import overtime


class _StubDB:
    def __init__(self, mashi, spshi):
        self._mashi = mashi
        self._spshi = spshi

    def _read(self, table):
        if table == "MASHI":
            return self._mashi
        if table == "SPSHI":
            return self._spshi
        return []


def test_calc_overtime_sums_mashi_and_spshi(monkeypatch):
    emp = {"ID": 5, "HRSWEEK": 40}
    mashi = [
        {"DATE": "2024-03-04", "EMPLOYEEID": 5, "SHIFTID": 1},   # known shift → 8h
        {"DATE": "2024-03-05", "EMPLOYEEID": 5, "SHIFTID": 99},  # unknown shift → 0h
        {"DATE": "2024-03-06", "EMPLOYEEID": 6, "SHIFTID": 1},   # other employee → skipped
        {"DATE": "", "EMPLOYEEID": 5, "SHIFTID": 1},             # empty date → skipped
    ]
    spshi = [{"DATE": "2024-03-07", "EMPLOYEEID": 5, "DURATION": 6.0}]
    shifts_map = {1: {"DURATION0": 8.0}}
    monkeypatch.setattr(overtime, "get_db", lambda: _StubDB(mashi, spshi))

    res = overtime._calc_overtime(emp, 2024, 3, shifts_map)

    assert res["contract_hours"] == 40.0
    assert res["shifts_count"] == 3          # 2 MASHI (emp 5) + 1 SPSHI
    assert res["actual_hours"] == 14.0       # 8 + 0 + 6
    assert res["expected_hours"] > 0
    assert res["difference"] == round(14.0 - res["expected_hours"], 2)


def test_calc_overtime_zero_contract(monkeypatch):
    """No HRSWEEK → expected 0, and no matching records → actual 0."""
    monkeypatch.setattr(overtime, "get_db", lambda: _StubDB([], []))
    res = overtime._calc_overtime({"ID": 1, "HRSWEEK": 0}, 2024, 3, {})
    assert res["expected_hours"] == 0.0
    assert res["actual_hours"] == 0.0
    assert res["shifts_count"] == 0


@pytest.mark.parametrize("year,month,expected", [(2024, 3, 21), (2024, 2, 21)])
def test_count_working_days_mon_fri(year, month, expected):
    assert overtime._count_working_days_mon_fri(year, month) == expected
