"""Unit tests for the pure absence-statistics helpers in api.routers.absences.

These exercise _classify_leave_type and _build_employee_stats directly (no HTTP /
DB), covering the vacation/sick/other classification, monthly bucketing, pending
counting (both dict and legacy-string status), and the date guards.
"""

from api.routers.absences import _build_employee_stats, _classify_leave_type


class TestClassifyLeaveType:
    def test_none_is_other(self):
        assert _classify_leave_type(None) == "other"

    def test_entitled_is_vacation(self):
        assert _classify_leave_type({"ENTITLED": True}) == "vacation"

    def test_sick_by_name(self):
        assert _classify_leave_type({"NAME": "Krankheit"}) == "sick"

    def test_sick_by_shortname(self):
        assert _classify_leave_type({"SHORTNAME": "KU"}) == "sick"

    def test_plain_is_other(self):
        assert _classify_leave_type({"NAME": "Sonderurlaub", "SHORTNAME": "SU"}) == "other"


class TestBuildEmployeeStats:
    def _lt_map(self):
        return {
            10: {"ENTITLED": True},          # vacation
            20: {"NAME": "Krank"},           # sick
            30: {"NAME": "Sonstiges"},       # other
        }

    def test_aggregates_categories_months_and_pending(self):
        absences = [
            {"employee_id": 1, "date": "2024-03-01", "leave_type_id": 10, "id": 100},  # vacation + pending(dict)
            {"employee_id": 1, "date": "2024-03-05", "leave_type_id": 20, "id": 101},  # sick + pending(legacy str)
            {"employee_id": 1, "date": "2024-04-10", "leave_type_id": 30, "id": 102},  # other
            {"employee_id": 2, "date": "2024-03-01", "leave_type_id": 10},             # wrong employee
            {"employee_id": 1, "date": "2023-12-31", "leave_type_id": 10},             # wrong year
            {"employee_id": 1, "date": "2024-XX-01", "leave_type_id": 10, "id": 104},  # bad month → guard
        ]
        status_data = {"100": {"status": "pending"}, "101": "pending"}
        s = _build_employee_stats(1, 2024, absences, self._lt_map(), status_data)

        assert s["employee_id"] == 1 and s["year"] == 2024
        assert s["vacation_days"] == 1
        assert s["sick_days"] == 1
        assert s["other_days"] == 1
        assert s["total_days"] == 3
        assert s["pending_requests"] == 2  # dict + legacy-string pending
        assert len(s["by_month"]) == 12

        march = next(m for m in s["by_month"] if m["month"] == 3)
        assert march["vacation"] == 1 and march["sick"] == 1
        april = next(m for m in s["by_month"] if m["month"] == 4)
        assert april["other"] == 1

    def test_no_matching_absences_is_zero(self):
        s = _build_employee_stats(99, 2024, [], self._lt_map(), {})
        assert s["total_days"] == 0
        assert s["pending_requests"] == 0
        assert all(m["vacation"] == 0 and m["sick"] == 0 and m["other"] == 0 for m in s["by_month"])
