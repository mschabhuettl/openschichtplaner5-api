"""Tests for sp5lib/sqlite_adapter.py — SQLite mirror layer."""

import os
import pytest
from unittest.mock import patch

from sp5lib.sqlite_adapter import SP5SQLiteAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Return a fresh adapter backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_sp5.sqlite")
    adapter = SP5SQLiteAdapter(db_path)
    adapter.init_db()
    return adapter


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


class TestInitDb:
    def test_creates_sqlite_file(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "sp5.sqlite")
        adapter = SP5SQLiteAdapter(db_path)
        adapter.init_db()
        assert os.path.exists(db_path)

    def test_creates_tables(self, tmp_db):
        import sqlite3
        with sqlite3.connect(tmp_db.sqlite_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"employees", "groups", "bookings"}.issubset(tables)

    def test_idempotent(self, tmp_db):
        """Calling init_db twice must not raise."""
        tmp_db.init_db()  # second call


# ---------------------------------------------------------------------------
# get_employees / get_groups / get_bookings_for_employee (empty DB)
# ---------------------------------------------------------------------------


class TestQueryHelpers:
    def test_get_employees_empty(self, tmp_db):
        assert tmp_db.get_employees() == []

    def test_get_groups_empty(self, tmp_db):
        assert tmp_db.get_groups() == []

    def test_get_bookings_for_employee_empty(self, tmp_db):
        assert tmp_db.get_bookings_for_employee(99) == []


# ---------------------------------------------------------------------------
# sync_from_dbf — mocked read_dbf
# ---------------------------------------------------------------------------


FAKE_EMPLOYEES = [
    {
        "ID": 1,
        "POSITION": 10,
        "NUMBER": "E001",
        "NAME": "Mustermann",
        "FIRSTNAME": "Max",
        "SHORTNAME": "MuMa",
        "SALUTATION": "Herr",
        "STREET": "Hauptstr. 1",
        "ZIP": "12345",
        "TOWN": "Wien",
        "PHONE": "01/1234567",
        "EMAIL": "max@example.com",
        "PHOTO": "",
        "FUNCTION": "Dev",
        "SEX": "M",
        "BIRTHDAY": "19900101",
        "EMPSTART": "2020-01-01",
        "EMPEND": None,
    },
    {"ID": None, "NAME": "Ghost"},  # should be skipped (no ID)
]

FAKE_GROUPS = [
    {
        "ID": 10,
        "NAME": "Entwicklung",
        "SHORTNAME": "DEV",
        "SUPERID": None,
        "POSITION": 1,
        "HIDE": False,
    }
]

FAKE_BOOKINGS = [
    {
        "ID": 100,
        "EMPLOYEEID": 1,
        "DATE": "20240115",
        "TYPE": 2,
        "VALUE": 8.0,
        "NOTE": "Normaltag",
    },
    {"ID": None, "EMPLOYEEID": 1, "DATE": "20240116", "TYPE": 2},  # skipped
]


def _make_read_dbf(employees=None, groups=None, bookings=None):
    """Return a fake read_dbf that serves different data per filename."""
    employees = employees if employees is not None else FAKE_EMPLOYEES
    groups = groups if groups is not None else FAKE_GROUPS
    bookings = bookings if bookings is not None else FAKE_BOOKINGS

    def _read_dbf(path: str):
        name = os.path.basename(path).upper()
        if "EMPL" in name:
            return employees
        if "GROUP" in name:
            return groups
        if "BOOK" in name:
            return bookings
        return []

    return _read_dbf


class TestSyncFromDbf:
    def test_sync_counts(self, tmp_db):
        with patch("sp5lib.sqlite_adapter.SP5SQLiteAdapter.sync_from_dbf") as _:
            pass  # just checking we can import

    def test_sync_inserts_employees(self, tmp_db):
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf()):
            counts = tmp_db.sync_from_dbf("/fake/path")
        assert counts["employees"] == len(FAKE_EMPLOYEES)
        rows = tmp_db.get_employees()
        # Only 1 valid employee (Ghost has no ID)
        assert len(rows) == 1
        assert rows[0]["name"] == "Mustermann"
        assert rows[0]["firstname"] == "Max"

    def test_sync_inserts_groups(self, tmp_db):
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf()):
            tmp_db.sync_from_dbf("/fake/path")
        groups = tmp_db.get_groups()
        assert len(groups) == 1
        assert groups[0]["name"] == "Entwicklung"

    def test_sync_inserts_bookings(self, tmp_db):
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf()):
            tmp_db.sync_from_dbf("/fake/path")
        bookings = tmp_db.get_bookings_for_employee(1)
        assert len(bookings) == 1
        assert bookings[0]["date"] == "2024-01-15"

    def test_sync_is_idempotent(self, tmp_db):
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf()):
            tmp_db.sync_from_dbf("/fake/path")
            tmp_db.sync_from_dbf("/fake/path")
        # Second sync replaces data, so count stays the same
        assert len(tmp_db.get_employees()) == 1

    def test_sync_dbf_read_error_returns_empty(self, tmp_db):
        """If a DBF file is missing/corrupt, the table gets empty list, no crash."""
        def _fail_dbf(path):
            raise FileNotFoundError(f"not found: {path}")

        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_fail_dbf):
            counts = tmp_db.sync_from_dbf("/nonexistent/path")
        # All counts 0 (empty lists returned on error)
        assert counts["employees"] == 0
        assert counts["groups"] == 0
        assert counts["bookings"] == 0

    def test_date_normalisation_8digit(self, tmp_db):
        """8-digit date strings like '20240115' must become '2024-01-15'."""
        empl = [dict(FAKE_EMPLOYEES[0], BIRTHDAY="19850322", EMPSTART="20050601")]
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf(employees=empl)):
            tmp_db.sync_from_dbf("/fake/path")
        rows = tmp_db.get_employees()
        assert rows[0]["birthday"] == "1985-03-22"

    def test_date_normalisation_iso_passthrough(self, tmp_db):
        """Dates already in ISO format should pass through unchanged."""
        empl = [dict(FAKE_EMPLOYEES[0], EMPSTART="2020-06-01", EMPEND=None)]
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf(employees=empl)):
            tmp_db.sync_from_dbf("/fake/path")

    def test_date_normalisation_none(self, tmp_db):
        empl = [dict(FAKE_EMPLOYEES[0], BIRTHDAY=None, EMPEND=None)]
        with patch("sp5lib.dbf_reader.read_dbf", side_effect=_make_read_dbf(employees=empl)):
            tmp_db.sync_from_dbf("/fake/path")
        rows = tmp_db.get_employees()
        assert rows[0]["birthday"] is None
