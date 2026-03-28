"""
Tests for PostgreSQL backend (SP5PostgresDatabase).

Uses SQLite in-memory as a stand-in for PostgreSQL (same SQLAlchemy ORM).
This validates the PostgreSQL database class API without requiring a running PG instance.
"""

import os

import pytest
from sp5lib.pg_database import SP5PostgresDatabase


@pytest.fixture
def pg_db(tmp_path):
    """Create a fresh SQLite-backed SP5PostgresDatabase for testing."""
    db_path = str(tmp_path / "test.db")
    db = SP5PostgresDatabase(f"sqlite:///{db_path}")
    db.init_db()
    return db


class TestEmployees:
    def test_create_and_get_employee(self, pg_db):
        result = pg_db.create_employee({"NAME": "Müller", "FIRSTNAME": "Hans"})
        assert result["NAME"] == "Müller"
        assert "id" in result

        employees = pg_db.get_employees()
        assert len(employees) == 1
        assert employees[0]["NAME"] == "Müller"
        assert employees[0]["FIRSTNAME"] == "Hans"

    def test_update_employee(self, pg_db):
        created = pg_db.create_employee({"NAME": "Schmidt", "FIRSTNAME": "Anna"})
        emp_id = created.get("id") or created.get("ID")

        pg_db.update_employee(emp_id, {"FIRSTNAME": "Marie"})

        emp = pg_db.get_employee(emp_id)
        assert emp is not None
        assert emp["FIRSTNAME"] == "Marie"

    def test_soft_delete_employee(self, pg_db):
        created = pg_db.create_employee({"NAME": "Weber"})
        emp_id = created.get("id") or created.get("ID")

        assert pg_db.delete_employee(emp_id) == 1
        assert len(pg_db.get_employees()) == 0
        assert len(pg_db.get_employees(include_hidden=True)) == 1

    def test_activate_employee(self, pg_db):
        created = pg_db.create_employee({"NAME": "Fischer"})
        emp_id = created.get("id") or created.get("ID")
        pg_db.delete_employee(emp_id)
        assert len(pg_db.get_employees()) == 0

        pg_db.activate_employee(emp_id)
        assert len(pg_db.get_employees()) == 1

    def test_shortname_auto_generation(self, pg_db):
        pg_db.create_employee({"NAME": "Müller", "FIRSTNAME": "Hans"})
        employees = pg_db.get_employees()
        assert employees[0]["SHORTNAME"] == "HMÜ"
        assert employees[0]["SHORTNAME_GENERATED"] is True

    def test_duplicate_shortname_rejected(self, pg_db):
        pg_db.create_employee({"NAME": "A", "SHORTNAME": "TST"})
        with pytest.raises(ValueError, match="DUPLICATE:SHORTNAME"):
            pg_db.create_employee({"NAME": "B", "SHORTNAME": "TST"})


class TestGroups:
    def test_create_and_get_groups(self, pg_db):
        pg_db.create_group({"NAME": "Frühschicht", "SHORTNAME": "FS"})
        groups = pg_db.get_groups()
        assert len(groups) == 1
        assert groups[0]["NAME"] == "Frühschicht"

    def test_group_members(self, pg_db):
        emp = pg_db.create_employee({"NAME": "Test"})
        grp = pg_db.create_group({"NAME": "Gruppe A"})
        emp_id = emp.get("id") or emp.get("ID")
        grp_id = grp.get("id") or grp.get("ID")

        pg_db.add_group_member(grp_id, emp_id)
        members = pg_db.get_group_members(grp_id)
        assert emp_id in members

        pg_db.remove_group_member(grp_id, emp_id)
        assert pg_db.get_group_members(grp_id) == []


class TestShifts:
    def test_create_and_get_shifts(self, pg_db):
        pg_db.create_shift({"NAME": "Frühschicht", "SHORTNAME": "FS", "DURATION0": 8.0})
        shifts = pg_db.get_shifts()
        assert len(shifts) == 1
        assert shifts[0]["NAME"] == "Frühschicht"
        assert shifts[0]["DURATION0"] == 8.0

    def test_duplicate_shift_name(self, pg_db):
        pg_db.create_shift({"NAME": "Nachtschicht"})
        with pytest.raises(ValueError, match="DUPLICATE:SHIFTNAME"):
            pg_db.create_shift({"NAME": "Nachtschicht"})


class TestSchedule:
    def test_add_and_get_schedule(self, pg_db):
        emp = pg_db.create_employee({"NAME": "Worker"})
        shift = pg_db.create_shift({"NAME": "Day", "DURATION0": 8.0})
        emp_id = emp.get("id") or emp.get("ID")
        shift_id = shift.get("id") or shift.get("ID")

        pg_db.add_schedule_entry(emp_id, "2026-03-15", shift_id)
        entries = pg_db.get_schedule(2026, 3)
        assert len(entries) == 1
        assert entries[0]["employee_id"] == emp_id

    def test_duplicate_schedule_entry_rejected(self, pg_db):
        emp = pg_db.create_employee({"NAME": "Worker"})
        shift = pg_db.create_shift({"NAME": "Day"})
        emp_id = emp.get("id") or emp.get("ID")
        shift_id = shift.get("id") or shift.get("ID")

        pg_db.add_schedule_entry(emp_id, "2026-03-15", shift_id)
        with pytest.raises(ValueError, match="already exists"):
            pg_db.add_schedule_entry(emp_id, "2026-03-15", shift_id)

    def test_delete_schedule_entry(self, pg_db):
        emp = pg_db.create_employee({"NAME": "Worker"})
        shift = pg_db.create_shift({"NAME": "Day"})
        emp_id = emp.get("id") or emp.get("ID")
        shift_id = shift.get("id") or shift.get("ID")

        pg_db.add_schedule_entry(emp_id, "2026-03-15", shift_id)
        deleted = pg_db.delete_schedule_entry(emp_id, "2026-03-15")
        assert deleted >= 1
        assert pg_db.get_schedule(2026, 3) == []


class TestAbsences:
    def test_add_absence(self, pg_db):
        emp = pg_db.create_employee({"NAME": "Worker"})
        lt = pg_db.create_leave_type({"NAME": "Urlaub", "ENTITLED": True})
        emp_id = emp.get("id") or emp.get("ID")
        lt_id = lt.get("id") or lt.get("ID")

        result = pg_db.add_absence(emp_id, "2026-03-15", lt_id)
        assert result["EMPLOYEEID"] == emp_id


class TestUsers:
    def test_create_user_and_login(self, pg_db):
        pg_db.create_user({"NAME": "admin", "PASSWORD": "secret123", "role": "Admin"})
        users = pg_db.get_users()
        assert len(users) == 1
        assert users[0]["NAME"] == "admin"
        assert users[0]["role"] == "Admin"

        # Verify password
        user = pg_db.verify_user_password("admin", "secret123")
        assert user is not None
        assert user["NAME"] == "admin"

        # Wrong password
        assert pg_db.verify_user_password("admin", "wrong") is None

    def test_duplicate_username(self, pg_db):
        pg_db.create_user({"NAME": "testuser", "PASSWORD": "pass"})
        with pytest.raises(ValueError, match="DUPLICATE:USERNAME"):
            pg_db.create_user({"NAME": "testuser", "PASSWORD": "pass"})


class TestStats:
    def test_get_stats(self, pg_db):
        pg_db.create_employee({"NAME": "A"})
        pg_db.create_employee({"NAME": "B"})
        pg_db.create_shift({"NAME": "Day"})

        stats = pg_db.get_stats()
        assert stats["employees"] == 2
        assert stats["shifts"] == 1


class TestHolidays:
    def test_holidays_crud(self, pg_db):
        h = pg_db.create_holiday({"DATE": "2026-12-25", "NAME": "Weihnachten", "INTERVAL": 1})
        holidays = pg_db.get_holidays()
        assert len(holidays) == 1

        pg_db.update_holiday(h["id"], {"NAME": "Christtag"})
        holidays = pg_db.get_holidays()
        assert holidays[0]["NAME"] == "Christtag"

        pg_db.delete_holiday(h["id"])
        assert pg_db.get_holidays() == []

    def test_holidays_year_filter(self, pg_db):
        pg_db.create_holiday({"DATE": "2026-01-01", "NAME": "Neujahr", "INTERVAL": 1})
        pg_db.create_holiday({"DATE": "2026-05-01", "NAME": "Tag der Arbeit", "INTERVAL": 0})

        holidays_2026 = pg_db.get_holidays(2026)
        assert len(holidays_2026) == 2

        holidays_2027 = pg_db.get_holidays(2027)
        assert len(holidays_2027) == 1  # Only recurring


class TestChangelog:
    def test_log_and_get_changelog(self, pg_db):
        pg_db.log_action("admin", "CREATE", "employee", 1, details="Created employee")
        entries = pg_db.get_changelog()
        assert len(entries) == 1
        assert entries[0]["action"] == "CREATE"


class TestLeaveTypes:
    def test_leave_type_crud(self, pg_db):
        lt = pg_db.create_leave_type({"NAME": "Urlaub", "SHORTNAME": "U", "ENTITLED": True, "STDENTIT": 25.0})
        assert lt["NAME"] == "Urlaub"

        lts = pg_db.get_leave_types()
        assert len(lts) == 1
        assert lts[0]["ENTITLED"] is True

        pg_db.update_leave_type(lt["id"], {"STDENTIT": 30.0})
        pg_db.hide_leave_type(lt["id"])
        assert len(pg_db.get_leave_types()) == 0


class TestNotes:
    def test_notes_crud(self, pg_db):
        n = pg_db.add_note("2026-03-15", "Test note", employee_id=0)
        assert n["text1"] == "Test note"

        notes = pg_db.get_notes(date="2026-03-15")
        assert len(notes) == 1

        pg_db.delete_note(n["id"])
        assert pg_db.get_notes(date="2026-03-15") == []


class TestBackendSwitch:
    """Test that DB_BACKEND env variable controls which backend is used."""

    def test_default_is_dbf(self):
        from sp5lib.db_config import get_db_backend
        # Unset any DB_BACKEND
        old = os.environ.pop("DB_BACKEND", None)
        try:
            assert get_db_backend() == "dbf"
        finally:
            if old:
                os.environ["DB_BACKEND"] = old

    def test_postgresql_backend(self):
        from sp5lib.db_config import get_db_backend, is_postgresql
        old = os.environ.get("DB_BACKEND")
        os.environ["DB_BACKEND"] = "postgresql"
        try:
            assert get_db_backend() == "postgresql"
            assert is_postgresql() is True
        finally:
            if old:
                os.environ["DB_BACKEND"] = old
            else:
                os.environ.pop("DB_BACKEND", None)
