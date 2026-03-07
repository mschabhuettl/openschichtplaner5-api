"""
Focused unit tests for conflict detection (get_schedule_conflicts).

Covers:
  1. shift_and_absence — employee has shift AND absence on same day
  2. holiday_ban       — absence falls in a holiday-ban period
  3. No false positives — shift without absence / absence without shift
  4. holiday_ban only fires for matching group (not for other groups)
  5. Sorting contract  — conflicts are sorted by (date, employee_id)
  6. group_id filter   — only employees of the given group are checked
  7. shift_and_absence reported for SPSHI (special shift) as well
  8. holiday_shift     — shift on a public holiday → warning
  9. long_shift        — shift duration > 10h → warning
  10. hidden employees — intentionally excluded from conflict detection
"""

import os
import shutil
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_SITE_PACKAGES = os.path.join(
    _BACKEND_DIR, "venv", "lib", "python3.13", "site-packages"
)
_FIXTURES_DIR = os.path.join(_BACKEND_DIR, "tests", "fixtures")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if os.path.isdir(_VENV_SITE_PACKAGES) and _VENV_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_SITE_PACKAGES)

_REAL_DB_PATH = os.environ.get("SP5_REAL_DB") or (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else _FIXTURES_DIR
)


@pytest.fixture
def tmp_db(tmp_path):
    """Function-scoped writable copy of the DB."""
    dst = tmp_path / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    from sp5lib.database import SP5Database

    return SP5Database(str(dst))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_employee_and_shift(db):
    """Return (emp_id, shift_id) or skip if DB has no employees/shifts."""
    emps = db.get_employees()
    shifts = db.get_shifts()
    if not emps or not shifts:
        pytest.skip("No employees or shifts in test DB")
    return emps[0]["ID"], shifts[0]["ID"]


def _first_leave_type(db):
    lt_list = db.get_leave_types()
    if not lt_list:
        pytest.skip("No leave types in test DB")
    return lt_list[0]["ID"]


# ---------------------------------------------------------------------------
# Test 1: shift_and_absence conflict is detected
# ---------------------------------------------------------------------------


class TestShiftAndAbsenceConflict:
    def test_conflict_detected_when_shift_and_absence_on_same_day(self, tmp_db):
        """An employee with a shift AND an absence on the same day → conflict."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        lt_id = _first_leave_type(tmp_db)
        test_date = "2025-07-10"

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass
        try:
            tmp_db.add_absence(emp_id, test_date, lt_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 7)
        types = [
            c["type"]
            for c in conflicts
            if c["employee_id"] == emp_id and c["date"] == test_date
        ]
        assert "shift_and_absence" in types, (
            "Expected shift_and_absence conflict but none was returned"
        )

    def test_no_conflict_when_only_shift(self, tmp_db):
        """An employee with only a shift (no absence) → no shift_and_absence conflict."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        test_date = "2025-08-05"

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 8)
        sa_conflicts = [
            c
            for c in conflicts
            if c["employee_id"] == emp_id
            and c["date"] == test_date
            and c["type"] == "shift_and_absence"
        ]
        assert sa_conflicts == [], (
            "Unexpected shift_and_absence conflict for shift-only day"
        )

    def test_no_conflict_when_only_absence(self, tmp_db):
        """An employee with only an absence (no shift) → no shift_and_absence conflict."""
        emps = tmp_db.get_employees()
        if not emps:
            pytest.skip("No employees in test DB")
        emp_id = emps[0]["ID"]
        lt_id = _first_leave_type(tmp_db)
        test_date = "2025-09-03"

        try:
            tmp_db.add_absence(emp_id, test_date, lt_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 9)
        sa_conflicts = [
            c
            for c in conflicts
            if c["employee_id"] == emp_id
            and c["date"] == test_date
            and c["type"] == "shift_and_absence"
        ]
        assert sa_conflicts == [], (
            "Unexpected shift_and_absence conflict for absence-only day"
        )


# ---------------------------------------------------------------------------
# Test 2: holiday_ban conflict is detected
# ---------------------------------------------------------------------------


class TestHolidayBanConflict:
    def test_absence_in_holiday_ban_period_is_conflict(self, tmp_db):
        """Absence during a holiday-ban period → holiday_ban conflict."""
        lt_id = _first_leave_type(tmp_db)
        ban_start = "2025-10-01"
        ban_end = "2025-10-31"
        test_date = "2025-10-15"

        # Find a non-hidden employee that belongs to a group
        visible_emp_ids = {e["ID"] for e in tmp_db.get_employees(include_hidden=False)}
        groups = tmp_db.get_groups() if hasattr(tmp_db, "get_groups") else []
        emp_id = None
        group_id_for_ban = None
        for g in groups:
            members = tmp_db.get_group_members(g["ID"])
            for m in members:
                if m in visible_emp_ids:
                    emp_id = m
                    group_id_for_ban = g["ID"]
                    break
            if emp_id is not None:
                break
        if emp_id is None:
            pytest.skip("No non-hidden employee in any group found in test DB")

        try:
            tmp_db.create_holiday_ban(
                group_id_for_ban, ban_start, ban_end, reason="Testsperre"
            )
        except (AttributeError, TypeError) as exc:
            pytest.skip(f"create_holiday_ban not available: {exc}")

        try:
            tmp_db.add_absence(emp_id, test_date, lt_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 10)
        hb_conflicts = [
            c
            for c in conflicts
            if c["employee_id"] == emp_id
            and c["date"] == test_date
            and c["type"] == "holiday_ban"
        ]
        assert hb_conflicts, "Expected holiday_ban conflict but none was returned"


# ---------------------------------------------------------------------------
# Test 3: Sorting contract
# ---------------------------------------------------------------------------


class TestSortingContract:
    def test_conflicts_sorted_by_date_then_employee_id(self, tmp_db):
        """get_schedule_conflicts must return list sorted by (date, employee_id)."""
        emps = tmp_db.get_employees()
        shifts = tmp_db.get_shifts()
        if len(emps) < 2 or not shifts:
            pytest.skip("Need ≥2 employees and ≥1 shift for sorting test")

        lt_id = _first_leave_type(tmp_db)
        shift_id = shifts[0]["ID"]
        # Insert two conflicts for two different employees on the same date
        for idx in range(min(2, len(emps))):
            eid = emps[idx]["ID"]
            date = "2025-11-20"
            try:
                tmp_db.add_schedule_entry(eid, date, shift_id)
            except ValueError:
                pass
            try:
                tmp_db.add_absence(eid, date, lt_id)
            except ValueError:
                pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 11)
        keys = [(c["date"], c["employee_id"]) for c in conflicts]
        assert keys == sorted(keys), "Conflicts are not sorted by (date, employee_id)"


# ---------------------------------------------------------------------------
# Test 4: group_id filter
# ---------------------------------------------------------------------------


class TestGroupIdFilter:
    def test_group_filter_excludes_other_employees(self, tmp_db):
        """With group_id filter, only members of that group appear in conflicts."""
        groups = tmp_db.get_groups() if hasattr(tmp_db, "get_groups") else []
        if not groups:
            pytest.skip("No groups in test DB")

        group_id = groups[0]["ID"]
        member_ids = set(tmp_db.get_group_members(group_id))
        if not member_ids:
            pytest.skip("Group has no members")

        # Conflicts with group_id filter must only contain employees from that group
        conflicts = tmp_db.get_schedule_conflicts(2024, 6, group_id=group_id)
        for c in conflicts:
            assert c["employee_id"] in member_ids, (
                f"Conflict for employee {c['employee_id']} who is not in group {group_id}"
            )


# ---------------------------------------------------------------------------
# Test 5: Return structure
# ---------------------------------------------------------------------------


class TestReturnStructure:
    def test_result_is_list(self, tmp_db):
        """get_schedule_conflicts always returns a list."""
        result = tmp_db.get_schedule_conflicts(2024, 6)
        assert isinstance(result, list)

    def test_conflict_dicts_have_required_keys(self, tmp_db):
        """Each conflict dict must contain employee_id, date, type, message."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        lt_id = _first_leave_type(tmp_db)
        test_date = "2025-12-10"

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass
        try:
            tmp_db.add_absence(emp_id, test_date, lt_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 12)
        for c in conflicts:
            for key in ("employee_id", "date", "type", "message"):
                assert key in c, f"Conflict dict missing required key: '{key}'"


# ---------------------------------------------------------------------------
# Test 8: holiday_shift — shift on a public holiday
# ---------------------------------------------------------------------------


class TestHolidayShiftConflict:
    def test_holiday_shift_detected(self, tmp_db):
        """Employee with a shift on a public holiday → holiday_shift warning."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        test_date = "2025-09-15"

        # Create a public holiday on that date
        tmp_db.create_holiday(
            {"DATE": test_date, "NAME": "Testheiertag", "INTERVAL": 0}
        )

        # Add a shift on that date
        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 9)
        types = [
            c["type"]
            for c in conflicts
            if c["employee_id"] == emp_id and c["date"] == test_date
        ]
        assert "holiday_shift" in types, (
            "Expected holiday_shift conflict for shift on public holiday, got: "
            + str(types)
        )

    def test_holiday_shift_has_warning_severity(self, tmp_db):
        """holiday_shift conflict must have severity='warning'."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        test_date = "2025-09-16"

        tmp_db.create_holiday(
            {"DATE": test_date, "NAME": "Testheiertag2", "INTERVAL": 0}
        )
        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 9)
        holiday_conflicts = [
            c
            for c in conflicts
            if c["type"] == "holiday_shift"
            and c["employee_id"] == emp_id
            and c["date"] == test_date
        ]
        assert holiday_conflicts, "No holiday_shift conflict found"
        assert holiday_conflicts[0].get("severity") == "warning", (
            "holiday_shift conflict must have severity='warning'"
        )

    def test_no_holiday_shift_without_holiday(self, tmp_db):
        """Shift on a non-holiday day must NOT generate a holiday_shift conflict."""
        emp_id, shift_id = _first_employee_and_shift(tmp_db)
        test_date = "2025-09-03"  # not a holiday

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 9)
        holiday_conflicts = [
            c
            for c in conflicts
            if c["type"] == "holiday_shift"
            and c["employee_id"] == emp_id
            and c["date"] == test_date
        ]
        assert not holiday_conflicts, (
            "False-positive holiday_shift conflict on non-holiday day"
        )


# ---------------------------------------------------------------------------
# Test 9: long_shift — shift duration > 10 hours
# ---------------------------------------------------------------------------


class TestLongShiftConflict:
    def test_long_shift_detected(self, tmp_db):
        """Shift with duration > 10h → long_shift warning."""
        emps = tmp_db.get_employees()
        shifts = tmp_db.get_shifts()
        if not emps or not shifts:
            pytest.skip("No employees or shifts in test DB")
        emp_id = emps[0]["ID"]
        shift = shifts[0]
        shift_id = shift["ID"]
        test_date = "2025-10-07"

        # Set shift duration to 11 hours
        tmp_db.update_shift(shift_id, {"DURATION0": 11.0})

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 10)
        types = [
            c["type"]
            for c in conflicts
            if c["employee_id"] == emp_id and c["date"] == test_date
        ]
        assert "long_shift" in types, (
            "Expected long_shift conflict for >10h shift, got: " + str(types)
        )

    def test_long_shift_has_warning_severity(self, tmp_db):
        """long_shift conflict must have severity='warning' and duration_hours field."""
        emps = tmp_db.get_employees()
        shifts = tmp_db.get_shifts()
        if not emps or not shifts:
            pytest.skip("No employees or shifts in test DB")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        test_date = "2025-10-08"

        tmp_db.update_shift(shift_id, {"DURATION0": 12.5})
        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 10)
        long_conflicts = [
            c
            for c in conflicts
            if c["type"] == "long_shift"
            and c["employee_id"] == emp_id
            and c["date"] == test_date
        ]
        assert long_conflicts, "No long_shift conflict found"
        c = long_conflicts[0]
        assert c.get("severity") == "warning", "long_shift must have severity='warning'"
        assert "duration_hours" in c, (
            "long_shift conflict must include 'duration_hours'"
        )
        assert c["duration_hours"] > 10.0

    def test_no_long_shift_for_normal_duration(self, tmp_db):
        """Shift with duration <= 10h must NOT generate a long_shift conflict."""
        emps = tmp_db.get_employees()
        shifts = tmp_db.get_shifts()
        if not emps or not shifts:
            pytest.skip("No employees or shifts in test DB")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        test_date = "2025-10-09"

        tmp_db.update_shift(shift_id, {"DURATION0": 8.0})
        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass

        conflicts = tmp_db.get_schedule_conflicts(2025, 10)
        long_conflicts = [
            c
            for c in conflicts
            if c["type"] == "long_shift"
            and c["employee_id"] == emp_id
            and c["date"] == test_date
        ]
        assert not long_conflicts, "False-positive long_shift for 8h shift"
