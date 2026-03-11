"""
Tests for the SQLAlchemy ORM layer (Q042).

Tests the ORM models, repository pattern, and database-agnostic query layer.
Uses in-memory SQLite — the same code would work against PostgreSQL.
"""

import pytest
from sp5lib.orm import get_engine, init_db
from sp5lib.orm.base import session_scope
from sp5lib.orm.models import Employee, Group, GroupAssignment
from sp5lib.orm.repository import EmployeeRepository, GroupRepository
from sqlalchemy import inspect


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine with all tables."""
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    """Provide a session that auto-rolls-back after each test."""
    from sp5lib.orm.base import get_session

    sess = get_session(engine)
    yield sess
    sess.rollback()
    sess.close()


# ── Schema Tests ─────────────────────────────────────────────────


class TestSchema:
    def test_tables_created(self, engine):
        """All four tables should exist after init_db."""
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "employees" in tables
        assert "groups" in tables
        assert "group_assignments" in tables
        assert "companies" in tables

    def test_init_db_idempotent(self, engine):
        """Calling init_db multiple times should not raise."""
        init_db(engine)
        init_db(engine)
        inspector = inspect(engine)
        assert "employees" in inspector.get_table_names()

    def test_employee_columns(self, engine):
        """Employee table should have all expected columns."""
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("employees")}
        expected = {
            "id", "position", "number", "name", "firstname", "shortname",
            "sex", "hrsday", "hrsweek", "hrsmonth", "workdays",
            "salutation", "street", "zip", "town", "phone", "email",
            "birthday", "empstart", "empend", "function", "hide",
            "note1", "note2", "note3", "note4", "created_at", "updated_at",
            "company_id",
        }
        assert expected.issubset(cols)


# ── Model Tests ──────────────────────────────────────────────────


class TestModels:
    def test_create_employee(self, session):
        emp = Employee(name="Müller", firstname="Hans", shortname="HMU")
        session.add(emp)
        session.flush()
        assert emp.id is not None
        assert emp.name == "Müller"
        assert emp.hide is False

    def test_employee_to_dict(self, session):
        emp = Employee(name="Test", firstname="User", hrsweek=38.5)
        session.add(emp)
        session.flush()
        d = emp.to_dict()
        assert d["NAME"] == "Test"
        assert d["FIRSTNAME"] == "User"
        assert d["HRSWEEK"] == 38.5
        assert d["HIDE"] is False

    def test_create_group(self, session):
        grp = Group(name="Frühschicht", shortname="FS")
        session.add(grp)
        session.flush()
        assert grp.id is not None
        assert grp.name == "Frühschicht"

    def test_group_to_dict(self, session):
        grp = Group(name="Nachtschicht", position=2)
        session.add(grp)
        session.flush()
        d = grp.to_dict()
        assert d["NAME"] == "Nachtschicht"
        assert d["POSITION"] == 2

    def test_group_assignment(self, session):
        emp = Employee(name="Test", firstname="Worker")
        grp = Group(name="Team A")
        session.add_all([emp, grp])
        session.flush()

        ga = GroupAssignment(employee_id=emp.id, group_id=grp.id)
        session.add(ga)
        session.flush()
        assert ga.id is not None
        assert ga.employee_id == emp.id
        assert ga.group_id == grp.id

    def test_group_hierarchy(self, session):
        """Groups can reference a parent group."""
        parent = Group(name="Produktion")
        session.add(parent)
        session.flush()

        child = Group(name="Linie 1", super_id=parent.id)
        session.add(child)
        session.flush()

        assert child.super_id == parent.id
        assert child.parent.name == "Produktion"

    def test_employee_repr(self, session):
        emp = Employee(id=42, name="Doe", firstname="Jane")
        assert "Doe" in repr(emp)
        assert "Jane" in repr(emp)

    def test_group_repr(self, session):
        grp = Group(id=7, name="Nacht")
        assert "Nacht" in repr(grp)


# ── Repository Tests ─────────────────────────────────────────────


class TestEmployeeRepository:
    def test_create_and_get(self, session):
        repo = EmployeeRepository(session)
        emp = repo.create(name="Schmidt", firstname="Karl", hrsweek=40)
        assert emp.id is not None

        fetched = repo.get_by_id(emp.id)
        assert fetched is not None
        assert fetched.name == "Schmidt"

    def test_get_all_excludes_hidden(self, session):
        repo = EmployeeRepository(session)
        repo.create(name="Active", firstname="A")
        repo.create(name="Hidden", firstname="B", hide=True)

        visible = repo.get_all(include_hidden=False)
        assert len(visible) == 1
        assert visible[0].name == "Active"

        all_emps = repo.get_all(include_hidden=True)
        assert len(all_emps) == 2

    def test_update(self, session):
        repo = EmployeeRepository(session)
        emp = repo.create(name="Before", firstname="X")
        updated = repo.update(emp.id, name="After", hrsweek=35)
        assert updated is not None
        assert updated.name == "After"
        assert updated.hrsweek == 35

    def test_update_nonexistent(self, session):
        repo = EmployeeRepository(session)
        result = repo.update(9999, name="Ghost")
        assert result is None

    def test_soft_delete(self, session):
        repo = EmployeeRepository(session)
        emp = repo.create(name="ToDelete", firstname="X")
        assert repo.soft_delete(emp.id) is True
        assert repo.get_all() == []
        assert len(repo.get_all(include_hidden=True)) == 1

    def test_soft_delete_nonexistent(self, session):
        repo = EmployeeRepository(session)
        assert repo.soft_delete(9999) is False

    def test_search(self, session):
        repo = EmployeeRepository(session)
        repo.create(name="Huber", firstname="Anna", shortname="AHU")
        repo.create(name="Maier", firstname="Franz", shortname="FMA")
        repo.create(name="Hubertus", firstname="Max", shortname="MHU")

        results = repo.search("hub")
        assert len(results) == 2  # Huber and Hubertus

        results = repo.search("FMA")
        assert len(results) == 1
        assert results[0].firstname == "Franz"

    def test_count(self, session):
        repo = EmployeeRepository(session)
        assert repo.count() == 0
        repo.create(name="A", firstname="1")
        repo.create(name="B", firstname="2")
        repo.create(name="C", firstname="3", hide=True)
        assert repo.count() == 2
        assert repo.count(include_hidden=True) == 3


class TestGroupRepository:
    def test_create_and_get(self, session):
        repo = GroupRepository(session)
        grp = repo.create(name="Frühschicht", shortname="FS")
        assert grp.id is not None

        fetched = repo.get_by_id(grp.id)
        assert fetched is not None
        assert fetched.name == "Frühschicht"

    def test_get_all_excludes_hidden(self, session):
        repo = GroupRepository(session)
        repo.create(name="Visible")
        repo.create(name="Hidden", hide=True)
        assert len(repo.get_all()) == 1
        assert len(repo.get_all(include_hidden=True)) == 2

    def test_update(self, session):
        repo = GroupRepository(session)
        grp = repo.create(name="Old")
        updated = repo.update(grp.id, name="New", position=5)
        assert updated is not None
        assert updated.name == "New"
        assert updated.position == 5

    def test_update_nonexistent(self, session):
        repo = GroupRepository(session)
        assert repo.update(9999, name="Ghost") is None

    def test_add_and_get_members(self, session):
        emp_repo = EmployeeRepository(session)
        grp_repo = GroupRepository(session)

        emp1 = emp_repo.create(name="Worker", firstname="One")
        emp2 = emp_repo.create(name="Worker", firstname="Two")
        grp = grp_repo.create(name="Team")

        grp_repo.add_member(grp.id, emp1.id)
        grp_repo.add_member(grp.id, emp2.id)

        members = grp_repo.get_members(grp.id)
        assert len(members) == 2
        member_ids = grp_repo.get_member_ids(grp.id)
        assert set(member_ids) == {emp1.id, emp2.id}

    def test_add_member_idempotent(self, session):
        emp_repo = EmployeeRepository(session)
        grp_repo = GroupRepository(session)

        emp = emp_repo.create(name="Worker", firstname="X")
        grp = grp_repo.create(name="Team")

        ga1 = grp_repo.add_member(grp.id, emp.id)
        ga2 = grp_repo.add_member(grp.id, emp.id)  # duplicate
        assert ga1.id == ga2.id  # same assignment returned

    def test_remove_member(self, session):
        emp_repo = EmployeeRepository(session)
        grp_repo = GroupRepository(session)

        emp = emp_repo.create(name="Worker", firstname="X")
        grp = grp_repo.create(name="Team")
        grp_repo.add_member(grp.id, emp.id)

        assert grp_repo.remove_member(grp.id, emp.id) is True
        assert grp_repo.get_members(grp.id) == []

    def test_remove_member_nonexistent(self, session):
        grp_repo = GroupRepository(session)
        assert grp_repo.remove_member(1, 999) is False

    def test_get_employee_groups(self, session):
        emp_repo = EmployeeRepository(session)
        grp_repo = GroupRepository(session)

        emp = emp_repo.create(name="Worker", firstname="Multi")
        g1 = grp_repo.create(name="Team A", position=1)
        g2 = grp_repo.create(name="Team B", position=2)

        grp_repo.add_member(g1.id, emp.id)
        grp_repo.add_member(g2.id, emp.id)

        groups = grp_repo.get_employee_groups(emp.id)
        assert len(groups) == 2
        assert {g.name for g in groups} == {"Team A", "Team B"}

    def test_soft_delete(self, session):
        repo = GroupRepository(session)
        grp = repo.create(name="ToDelete")
        assert repo.soft_delete(grp.id) is True
        assert repo.get_all() == []

    def test_soft_delete_nonexistent(self, session):
        repo = GroupRepository(session)
        assert repo.soft_delete(9999) is False


# ── Session Scope Tests ──────────────────────────────────────────


class TestSessionScope:
    def test_session_scope_commits(self, engine):
        """session_scope should auto-commit on success."""
        with session_scope(engine) as session:
            emp = Employee(name="Persistent", firstname="User")
            session.add(emp)

        # Verify in a new session
        from sp5lib.orm.base import get_session
        s2 = get_session(engine)
        result = s2.get(Employee, 1)
        assert result is not None
        assert result.name == "Persistent"
        s2.close()

    def test_session_scope_rollback_on_error(self, engine):
        """session_scope should rollback on exception."""
        try:
            with session_scope(engine) as session:
                session.add(Employee(name="WillRollback", firstname="X"))
                raise ValueError("Simulated error")
        except ValueError:
            pass

        from sp5lib.orm.base import get_session
        s2 = get_session(engine)
        result = s2.scalars(
            __import__("sqlalchemy").select(Employee)
        ).all()
        assert len(result) == 0
        s2.close()


# ── Engine Configuration Tests ───────────────────────────────────


class TestEngine:
    def test_sqlite_engine(self):
        """SQLite engine should be created without errors."""
        eng = get_engine("sqlite:///:memory:")
        assert eng is not None
        assert "sqlite" in str(eng.url)

    def test_engine_echo(self):
        """Engine with echo=True should not raise."""
        eng = get_engine("sqlite:///:memory:", echo=True)
        assert eng is not None


# ── Cascade / Relationship Tests ─────────────────────────────────


class TestCascades:
    def test_delete_employee_cascades_assignments(self, session):
        """Deleting an employee should cascade-delete group assignments."""
        emp = Employee(name="CascadeTest", firstname="X")
        grp = Group(name="CascadeGroup")
        session.add_all([emp, grp])
        session.flush()

        ga = GroupAssignment(employee_id=emp.id, group_id=grp.id)
        session.add(ga)
        session.flush()

        session.delete(emp)
        session.flush()

        # GroupAssignment should be gone
        from sqlalchemy import select
        remaining = session.scalars(select(GroupAssignment)).all()
        assert len(remaining) == 0

    def test_delete_group_cascades_assignments(self, session):
        """Deleting a group should cascade-delete group assignments."""
        emp = Employee(name="Worker", firstname="X")
        grp = Group(name="ToRemove")
        session.add_all([emp, grp])
        session.flush()

        session.add(GroupAssignment(employee_id=emp.id, group_id=grp.id))
        session.flush()

        session.delete(grp)
        session.flush()

        from sqlalchemy import select
        remaining = session.scalars(select(GroupAssignment)).all()
        assert len(remaining) == 0
