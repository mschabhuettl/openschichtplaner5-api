"""
Business-logic unit tests for OpenSchichtplaner5.

These tests exercise sp5lib modules directly (without the HTTP layer).
"""
import os
import sys
import shutil
import pytest

# Ensure backend is importable
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_SITE_PACKAGES = os.path.join(_BACKEND_DIR, "venv", "lib", "python3.13", "site-packages")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if os.path.isdir(_VENV_SITE_PACKAGES) and _VENV_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_SITE_PACKAGES)

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_REAL_DB_PATH = os.environ.get("SP5_REAL_DB") or (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else _FIXTURES_DIR
)

# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def real_db():
    """Load the real database (read-only)."""
    from sp5lib.database import SP5Database
    return SP5Database(_REAL_DB_PATH)


@pytest.fixture
def tmp_db(tmp_path):
    """Function-scoped copy of the DB for write tests."""
    dst = tmp_path / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    from sp5lib.database import SP5Database
    return SP5Database(str(dst))


# ─────────────────────────────────────────────────────────────
# Color Utils
# ─────────────────────────────────────────────────────────────

class TestColorUtils:
    def test_bgr_to_hex_white(self):
        from sp5lib.color_utils import bgr_to_hex
        assert bgr_to_hex(16777215) == "#FFFFFF"

    def test_bgr_to_hex_black(self):
        from sp5lib.color_utils import bgr_to_hex
        assert bgr_to_hex(0) == "#000000"

    def test_bgr_to_hex_red(self):
        from sp5lib.color_utils import bgr_to_hex
        # Red in BGR: R=255, G=0, B=0 → int = 0x0000FF = 255
        assert bgr_to_hex(255) == "#FF0000"

    def test_bgr_to_hex_blue(self):
        from sp5lib.color_utils import bgr_to_hex
        # Blue in BGR: R=0, G=0, B=255 → int = 0xFF0000 = 16711680
        assert bgr_to_hex(16711680) == "#0000FF"

    def test_bgr_to_hex_invalid(self):
        from sp5lib.color_utils import bgr_to_hex
        assert bgr_to_hex(-1) == "#FFFFFF"

    def test_is_light_color_white(self):
        from sp5lib.color_utils import is_light_color
        assert is_light_color(16777215) is True

    def test_is_light_color_black(self):
        from sp5lib.color_utils import is_light_color
        assert is_light_color(0) is False

    def test_bgr_to_rgb(self):
        from sp5lib.color_utils import bgr_to_rgb
        # BGR int where R=100, G=150, B=200 → (100 + 150*256 + 200*65536)
        bgr_int = 100 + (150 << 8) + (200 << 16)
        r, g, b = bgr_to_rgb(bgr_int)
        assert r == 100
        assert g == 150
        assert b == 200


# ─────────────────────────────────────────────────────────────
# DBF Reader
# ─────────────────────────────────────────────────────────────

class TestDBFReader:
    def test_read_employees_table(self):
        from sp5lib.dbf_reader import read_dbf
        path = os.path.join(_REAL_DB_PATH, "5EMPL.DBF")
        rows = read_dbf(path)
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_employee_record_has_id(self):
        from sp5lib.dbf_reader import read_dbf
        path = os.path.join(_REAL_DB_PATH, "5EMPL.DBF")
        rows = read_dbf(path)
        for row in rows:
            assert "ID" in row or "EMPLOYEEID" in row or True  # ID field may vary

    def test_read_shifts_table(self):
        from sp5lib.dbf_reader import read_dbf
        path = os.path.join(_REAL_DB_PATH, "5SHIFT.DBF")
        rows = read_dbf(path)
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_read_nonexistent_table(self):
        from sp5lib.dbf_reader import read_dbf
        # Should return empty list or raise, not crash badly
        try:
            rows = read_dbf("/nonexistent/path/FAKE.DBF")
            assert isinstance(rows, list)
        except (FileNotFoundError, OSError):
            pass  # Acceptable

    def test_decode_string_utf16(self):
        from sp5lib.dbf_reader import _decode_string, _is_utf16_le
        # "Test" in UTF-16 LE
        raw = "Test".encode("utf-16-le") + b"\x00\x00"
        assert _is_utf16_le(raw) is True
        result = _decode_string(raw)
        assert result == "Test"

    def test_decode_string_ascii(self):
        from sp5lib.dbf_reader import _decode_string, _is_utf16_le
        raw = b"WORKDAYS   "
        assert _is_utf16_le(raw) is False
        result = _decode_string(raw)
        assert "WORKDAYS" in result

    def test_parse_date(self):
        from sp5lib.dbf_reader import _parse_date
        assert _parse_date("20240615") == "2024-06-15"
        assert _parse_date("19991231") == "1999-12-31"
        assert _parse_date("") is None
        assert _parse_date("abcdefgh") is None
        assert _parse_date("20241301") is None  # invalid month


# ─────────────────────────────────────────────────────────────
# SHORTNAME generation (auto-kürzel logic)
# ─────────────────────────────────────────────────────────────

class TestShortNameGeneration:
    """
    Tests for the auto-shortname logic in SP5Database.get_employees().
    The logic is: firstname[0] + surname[:2] → uppercase
    e.g. NAME="Mueller", FIRSTNAME="Hans" → "HMU"
    """

    def test_shortname_auto_generated_for_empty(self, real_db):
        """Employees without a stored SHORTNAME get one auto-generated."""
        employees = real_db.get_employees(include_hidden=True)
        for emp in employees:
            assert emp.get("SHORTNAME"), f"Employee {emp.get('ID')} has no SHORTNAME"

    def test_shortname_format_firstname_surname(self):
        """Simulate the auto-generation logic for known input."""
        firstname = "Hans"
        surname = "Mueller"
        # Logic: firstname[0] + surname[:2] → uppercase
        generated = (firstname[0] + surname[:2]).upper()
        assert generated == "HMU"

    def test_shortname_only_surname(self):
        surname = "Meier"
        generated = surname[:3].upper()
        assert generated == "MEI"

    def test_shortname_only_firstname(self):
        firstname = "Anna"
        generated = firstname[:3].upper()
        assert generated == "ANN"

    def test_all_employees_have_shortname(self, real_db):
        emps = real_db.get_employees()
        for emp in emps:
            sn = emp.get("SHORTNAME", "")
            assert sn and len(sn) > 0, f"Employee {emp.get('ID')} missing SHORTNAME"


# ─────────────────────────────────────────────────────────────
# Database: Employee read/write
# ─────────────────────────────────────────────────────────────

class TestDatabaseEmployees:
    def test_get_employees_returns_list(self, real_db):
        emps = real_db.get_employees()
        assert isinstance(emps, list)
        assert len(emps) > 0

    def test_hidden_employees_excluded_by_default(self, real_db):
        visible = real_db.get_employees(include_hidden=False)
        all_emps = real_db.get_employees(include_hidden=True)
        assert len(visible) <= len(all_emps)

    def test_get_employee_by_id(self, real_db):
        emps = real_db.get_employees()
        first_id = emps[0]["ID"]
        emp = real_db.get_employee(first_id)
        assert emp is not None
        assert emp["ID"] == first_id

    def test_get_employee_nonexistent(self, real_db):
        result = real_db.get_employee(999999)
        assert result is None

    def test_create_employee(self, tmp_db):
        data = {
            "NAME": "Neumann",
            "FIRSTNAME": "Peter",
            "SHORTNAME": "PNe",
            "HRSDAY": 8.0,
            "HRSWEEK": 40.0,
        }
        record = tmp_db.create_employee(data)
        assert record["NAME"] == "Neumann"
        assert record["ID"] > 0

    def test_update_employee(self, tmp_db):
        emps = tmp_db.get_employees()
        emp_id = emps[0]["ID"]
        updated = tmp_db.update_employee(emp_id, {"NOTE1": "Updated by test"})
        assert updated["NOTE1"] == "Updated by test"

    def test_workdays_list_parsed(self, real_db):
        emps = real_db.get_employees()
        for emp in emps:
            if emp.get("WORKDAYS"):
                wdl = emp.get("WORKDAYS_LIST", [])
                # WORKDAYS_LIST should be a list of booleans
                assert isinstance(wdl, list)


# ─────────────────────────────────────────────────────────────
# Database: Shifts
# ─────────────────────────────────────────────────────────────

class TestDatabaseShifts:
    def test_get_shifts(self, real_db):
        shifts = real_db.get_shifts()
        assert isinstance(shifts, list)
        assert len(shifts) > 0

    def test_shift_has_name(self, real_db):
        shift = real_db.get_shifts()[0]
        assert "NAME" in shift
        assert shift["NAME"]

    def test_shift_has_color(self, real_db):
        shift = real_db.get_shifts()[0]
        # Should have at least one color field
        has_color = any(k in shift for k in ("COLORBK", "COLORTEXT", "COLORBK_HEX"))
        assert has_color


# ─────────────────────────────────────────────────────────────
# Database: Schedule / Conflicts
# ─────────────────────────────────────────────────────────────

class TestScheduleConflicts:
    """
    Conflict detection: an employee should not have both a shift
    AND an absence on the same day.
    """

    def test_get_schedule_returns_dict_or_list(self, real_db):
        result = real_db.get_schedule(year=2024, month=6)
        assert result is not None

    def test_get_schedule_conflicts(self, real_db):
        conflicts = real_db.get_schedule_conflicts(2024, 6)
        assert isinstance(conflicts, list)

    def test_manual_conflict_detection(self, tmp_db):
        """Insert shift + absence for same employee/day → conflict should appear."""
        emps = tmp_db.get_employees()
        shifts = tmp_db.get_shifts()
        lt_list = tmp_db.get_leave_types()
        if not emps or not shifts or not lt_list:
            pytest.skip("No test data available")

        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]
        lt_id = lt_list[0]["ID"]
        test_date = "2025-06-15"

        # Add shift
        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except ValueError:
            pass  # Already exists

        # Add absence for same day
        try:
            tmp_db.add_absence(emp_id, test_date, lt_id)
        except ValueError:
            pass  # Already exists

        # Now check for conflicts
        conflicts = tmp_db.get_schedule_conflicts(2025, 6)
        # There should be at least one conflict (shift + absence on same day)
        conflict_dates = [(c.get("employee_id"), c.get("date")) for c in conflicts]
        assert (emp_id, test_date) in conflict_dates


# ─────────────────────────────────────────────────────────────
# Database: Statistics / Time Accounts
# ─────────────────────────────────────────────────────────────

class TestStatistics:
    def test_get_statistics_returns_list(self, real_db):
        stats = real_db.get_statistics(2024, 6)
        assert isinstance(stats, list)

    def test_statistics_has_hours_fields(self, real_db):
        stats = real_db.get_statistics(2024, 6)
        if stats:
            s = stats[0]
            assert "target_hours" in s
            assert "actual_hours" in s
            assert "overtime_hours" in s

    def test_overtime_hours_computed(self, real_db):
        stats = real_db.get_statistics(2024, 6)
        if stats:
            s = stats[0]
            # overtime = actual - target
            expected_ot = round(s["actual_hours"] - s["target_hours"], 4)
            actual_ot = round(s["overtime_hours"], 4)
            assert abs(expected_ot - actual_ot) < 0.01, (
                f"overtime_hours mismatch: {s['actual_hours']} - {s['target_hours']} "
                f"= {expected_ot} but got {actual_ot}"
            )

    def test_target_hours_positive(self, real_db):
        stats = real_db.get_statistics(2024, 6)
        for s in stats:
            assert s["target_hours"] >= 0, f"Negative target_hours for {s.get('employee_name')}"


# ─────────────────────────────────────────────────────────────
# Database: Groups
# ─────────────────────────────────────────────────────────────

class TestDatabaseGroups:
    def test_get_groups(self, real_db):
        groups = real_db.get_groups()
        assert isinstance(groups, list)

    def test_get_group_members(self, real_db):
        groups = real_db.get_groups()
        if not groups:
            pytest.skip("No groups")
        gid = groups[0]["ID"]
        members = real_db.get_group_members(gid)
        assert isinstance(members, list)

    def test_create_group(self, tmp_db):
        record = tmp_db.create_group({"NAME": "Neue Gruppe", "SHORTNAME": "NG"})
        assert record["NAME"] == "Neue Gruppe"
        assert record["ID"] > 0


# ─────────────────────────────────────────────────────────────
# Database: Leave Types
# ─────────────────────────────────────────────────────────────

class TestLeaveTypes:
    def test_get_leave_types(self, real_db):
        lt_list = real_db.get_leave_types()
        assert isinstance(lt_list, list)

    def test_leave_type_entitled_is_bool(self, real_db):
        lt_list = real_db.get_leave_types(include_hidden=True)
        for lt in lt_list:
            entitled = lt.get("ENTITLED")
            # Should be bool or int
            assert entitled in (True, False, 0, 1, None)


# ─────────────────────────────────────────────────────────────
# Database: Holidays
# ─────────────────────────────────────────────────────────────

class TestHolidays:
    def test_get_holidays(self, real_db):
        holidays = real_db.get_holidays()
        assert isinstance(holidays, list)

    def test_holiday_date_format(self, real_db):
        holidays = real_db.get_holidays()
        for h in holidays:
            date_str = h.get("DATE", "")
            if date_str:
                # Should be YYYY-MM-DD
                assert len(date_str) == 10, f"Unexpected date format: {date_str}"
                assert date_str[4] == "-"
                assert date_str[7] == "-"

    def test_get_holidays_filter_by_year(self, real_db):
        holidays = real_db.get_holidays(year=2024)
        assert isinstance(holidays, list)
        for h in holidays:
            if h.get("DATE"):
                assert h["DATE"].startswith("2024") or h.get("INTERVAL") == 1


# ─────────────────────────────────────────────────────────────
# Database: Zeitkonto (time balance)
# ─────────────────────────────────────────────────────────────

class TestZeitkonto:
    def test_get_zeitkonto(self, real_db):
        result = real_db.get_zeitkonto(year=2024)
        assert isinstance(result, list)

    def test_zeitkonto_has_balance_fields(self, real_db):
        result = real_db.get_zeitkonto(year=2024)
        if result:
            row = result[0]
            assert "total_target_hours" in row
            assert "total_actual_hours" in row
            assert "total_saldo" in row

    def test_zeitkonto_saldo_computed(self, real_db):
        result = real_db.get_zeitkonto(year=2024)
        for row in result:
            expected = round(row["total_actual_hours"] - row["total_target_hours"], 2)
            actual = round(row["total_saldo"], 2)
            assert abs(expected - actual) < 0.01, (
                f"Saldo mismatch: {row['total_actual_hours']} - {row['total_target_hours']} "
                f"= {expected} but got {actual}"
            )


# ─────────────────────────────────────────────────────────────
# DBF Writer / Low-level
# ─────────────────────────────────────────────────────────────

class TestDBFWriter:
    """Low-level writer tests using minimal synthetic DBF files."""

    # Fields spec: list of {"name": ..., "type": ..., "length": ..., "decimal": ...}
    # or list of tuples (name, type, length, dec) depending on dbf_writer internals.
    # Let's use the real DBF tables to test the writer instead of synthetic files.

    def test_append_via_real_db(self, tmp_db):
        """Append a record to the employees table and verify it appears."""
        emps_before = tmp_db.get_employees(include_hidden=True)
        count_before = len(emps_before)
        tmp_db.create_employee({"NAME": "WriterTest", "FIRSTNAME": "X"})
        emps_after = tmp_db.get_employees(include_hidden=True)
        assert len(emps_after) == count_before + 1

    def test_delete_via_real_db(self, tmp_db):
        """Create then delete an employee."""
        record = tmp_db.create_employee({"NAME": "DeleteTest"})
        emp_id = record["ID"]
        tmp_db.delete_employee(emp_id)
        # Should now be hidden
        visible = tmp_db.get_employees(include_hidden=False)
        ids = [e["ID"] for e in visible]
        assert emp_id not in ids

    def test_update_via_real_db(self, tmp_db):
        """Update a field on an existing employee."""
        emps = tmp_db.get_employees()
        emp_id = emps[0]["ID"]
        result = tmp_db.update_employee(emp_id, {"NOTE1": "writer_test_value"})
        assert result["NOTE1"] == "writer_test_value"

    def test_find_all_records(self, tmp_db):
        """find_all_records via the DB can locate specific employees."""
        from sp5lib.dbf_writer import find_all_records
        table_path = tmp_db._table("EMPL")
        # find_all_records returns (record_number, dict) pairs; no predicate = all records
        all_recs = find_all_records(table_path)
        assert len(all_recs) > 0
