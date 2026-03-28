"""Tests for sp5lib.orm.sync — DBF → ORM sync utility.

Covers sync_employees, sync_groups, sync_group_assignments, sync_all.
Uses mocked DBF data to avoid filesystem dependencies.
"""

import os
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sp5lib.orm import get_engine, init_db
from sp5lib.orm.base import get_session
from sp5lib.orm.models import Employee, Group, GroupAssignment
from sp5lib.orm.sync import (
    _read_dbf,
    sync_all,
    sync_employees,
    sync_group_assignments,
    sync_groups,
)


@pytest.fixture
def engine():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    sess = get_session(engine)
    yield sess
    sess.rollback()
    sess.close()


# ── Sample DBF-like data ───────────────────────────────────────────────────────

SAMPLE_EMPLOYEES = [
    {
        "ID": 1, "POSITION": 1, "NUMBER": "E001", "NAME": "Müller",
        "FIRSTNAME": "Hans", "SHORTNAME": "HM", "SEX": 1,
        "HRSDAY": 8.0, "HRSWEEK": 40.0, "HRSMONTH": 173.0,
        "WORKDAYS": "MoDiMiDoFr", "SALUTATION": "Herr",
        "STREET": "Hauptstr 1", "ZIP": "8010", "TOWN": "Graz",
        "PHONE": "+43123", "EMAIL": "hm@test.at", "FUNCTION": "Techniker",
        "BIRTHDAY": "1990-01-15", "EMPSTART": "2020-01-01", "EMPEND": "",
        "HIDE": False, "NOTE1": "Note1", "NOTE2": "", "NOTE3": "", "NOTE4": "",
    },
    {
        "ID": 2, "POSITION": 2, "NUMBER": "E002", "NAME": "Schmidt",
        "FIRSTNAME": "Anna", "SHORTNAME": "AS", "SEX": 2,
        "HRSDAY": 6.0, "HRSWEEK": 30.0, "HRSMONTH": 130.0,
        "WORKDAYS": "MoDiMiDo", "SALUTATION": "Frau",
        "STREET": "", "ZIP": "", "TOWN": "", "PHONE": "", "EMAIL": "",
        "FUNCTION": "", "BIRTHDAY": None, "EMPSTART": None, "EMPEND": None,
        "HIDE": True, "NOTE1": "", "NOTE2": "", "NOTE3": "", "NOTE4": "",
    },
    # Row without ID — should be skipped
    {"POSITION": 3, "NAME": "NoID"},
]

SAMPLE_GROUPS = [
    {"ID": 10, "NAME": "Team Alpha", "SHORTNAME": "TA", "SUPERID": None, "POSITION": 1, "HIDE": False},
    {"ID": 20, "NAME": "Team Beta", "SHORTNAME": "TB", "SUPERID": 10, "POSITION": 2, "HIDE": True},
    {"NAME": "NoID Group"},  # should be skipped
]

SAMPLE_ASSIGNMENTS = [
    {"ID": 100, "EMPLOYEEID": 1, "GROUPID": 10},
    {"ID": 101, "EMPLOYEEID": 2, "GROUPID": 20},
    {"ID": 102, "EMPLOYEEID": 1, "GROUPID": 20},
    {"EMPLOYEEID": 999, "GROUPID": 10},  # no ID → skipped
    {"ID": 103},  # no EMPLOYEEID/GROUPID → skipped
]


def _mock_read_dbf(table_map):
    """Return a mock for _read_dbf that returns data from a dict of table_name → rows."""
    def fake_read_dbf(daten_path, table_name):
        return table_map.get(table_name, [])
    return fake_read_dbf


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestSyncEmployees:
    def test_sync_inserts_new_employees(self, session):
        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_EMPLOYEES):
            count = sync_employees(session, "/fake/path")
        assert count == 2  # 3rd row has no ID
        emp = session.get(Employee, 1)
        assert emp.name == "Müller"
        assert emp.firstname == "Hans"
        assert emp.hrsday == 8.0
        assert emp.hide is False

    def test_sync_updates_existing(self, session):
        # Pre-insert
        session.add(Employee(id=1, name="OldName", firstname="Old"))
        session.flush()

        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_EMPLOYEES):
            count = sync_employees(session, "/fake")
        assert count == 2
        emp = session.get(Employee, 1)
        assert emp.name == "Müller"  # updated

    def test_handles_null_fields(self, session):
        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_EMPLOYEES):
            sync_employees(session, "/fake")
        emp2 = session.get(Employee, 2)
        assert emp2.birthday is None
        assert emp2.empstart is None
        assert emp2.hide is True


class TestSyncGroups:
    def test_sync_inserts_groups(self, session):
        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_GROUPS):
            count = sync_groups(session, "/fake")
        assert count == 2
        g = session.get(Group, 10)
        assert g.name == "Team Alpha"
        assert g.super_id is None

        g2 = session.get(Group, 20)
        assert g2.super_id == 10
        assert g2.hide is True

    def test_sync_updates_existing_groups(self, session):
        session.add(Group(id=10, name="Old Group"))
        session.flush()

        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_GROUPS):
            count = sync_groups(session, "/fake")
        assert count == 2
        g = session.get(Group, 10)
        assert g.name == "Team Alpha"


class TestSyncGroupAssignments:
    def test_sync_assignments(self, session):
        # Need employees and groups first
        session.add(Employee(id=1, name="E1"))
        session.add(Employee(id=2, name="E2"))
        session.add(Group(id=10, name="G1"))
        session.add(Group(id=20, name="G2"))
        session.flush()

        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_ASSIGNMENTS):
            count = sync_group_assignments(session, "/fake")
        assert count == 3  # 2 skipped (no ID, no emp/group)

    def test_clears_old_assignments_first(self, session):
        session.add(Employee(id=1, name="E1"))
        session.add(Group(id=10, name="G1"))
        session.flush()
        session.add(GroupAssignment(id=999, employee_id=1, group_id=10))
        session.flush()
        assert session.query(GroupAssignment).count() == 1

        with patch("sp5lib.orm.sync._read_dbf", return_value=SAMPLE_ASSIGNMENTS[:1]):
            count = sync_group_assignments(session, "/fake")
        # Old 999 should be gone, only new 100
        assert count == 1
        assert session.query(GroupAssignment).filter_by(id=999).first() is None
        assert session.query(GroupAssignment).filter_by(id=100).first() is not None


class TestSyncAll:
    def test_sync_all_commits(self, engine):
        table_data = {
            "EMPL": SAMPLE_EMPLOYEES,
            "GROUP": SAMPLE_GROUPS,
            "GRASG": SAMPLE_ASSIGNMENTS[:3],
        }

        with patch("sp5lib.orm.sync._read_dbf", side_effect=lambda path, table: table_data.get(table, [])):
            stats = sync_all(engine, "/fake")

        assert stats["employees"] == 2
        assert stats["groups"] == 2
        assert stats["group_assignments"] == 3

        # Verify committed
        sess = get_session(engine)
        assert sess.query(Employee).count() == 2
        assert sess.query(Group).count() == 2
        sess.close()

    def test_sync_all_rollback_on_error(self, engine):
        def exploding_read(path, table):
            if table == "GRASG":
                raise RuntimeError("boom")
            return SAMPLE_EMPLOYEES if table == "EMPL" else SAMPLE_GROUPS

        with patch("sp5lib.orm.sync._read_dbf", side_effect=exploding_read):
            with pytest.raises(RuntimeError, match="boom"):
                sync_all(engine, "/fake")

        # Should have rolled back
        sess = get_session(engine)
        assert sess.query(Employee).count() == 0
        sess.close()


class TestReadDbf:
    def test_read_dbf_missing_file(self, tmp_path):
        # Should return empty list and log warning, not raise
        result = _read_dbf(str(tmp_path), "NONEXISTENT")
        assert result == []
