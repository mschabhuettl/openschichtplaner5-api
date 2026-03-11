"""
Tests for the Company model and migration (Q044 phase 1).
"""

import pytest
from sp5lib.orm import get_engine, init_db
from sp5lib.orm.base import get_session
from sp5lib.orm.models import Company, Employee
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def engine():
    """In-memory SQLite engine with all tables."""
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    sess = get_session(engine)
    yield sess
    sess.rollback()
    sess.close()


class TestCompanyModel:
    """Company CRUD and constraints."""

    def test_create_company(self, session):
        """Can create a Company with all fields."""
        c = Company(name="ACME Corp", slug="acme-corp")
        session.add(c)
        session.flush()
        assert c.id is not None
        assert c.name == "ACME Corp"
        assert c.slug == "acme-corp"
        assert c.is_active is True

    def test_duplicate_name_raises(self, session):
        """Unique constraint on name must prevent duplicates."""
        session.add(Company(name="Dup", slug="dup-1"))
        session.flush()
        session.add(Company(name="Dup", slug="dup-2"))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_duplicate_slug_raises(self, session):
        """Unique constraint on slug must prevent duplicates."""
        session.add(Company(name="First", slug="same"))
        session.flush()
        session.add(Company(name="Second", slug="same"))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_company_to_dict(self, session):
        c = Company(name="Dict Test", slug="dict-test")
        session.add(c)
        session.flush()
        d = c.to_dict()
        assert d["NAME"] == "Dict Test"
        assert d["SLUG"] == "dict-test"
        assert d["IS_ACTIVE"] is True

    def test_company_repr(self, session):
        c = Company(name="Repr Co", slug="repr")
        assert "Repr Co" in repr(c)


class TestEmployeeCompanyRelation:
    """Employee ↔ Company FK and relationship."""

    def test_employee_has_company_id_column(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("employees")}
        assert "company_id" in cols

    def test_employee_company_id_nullable(self, session):
        """Employees without a company should work (nullable FK)."""
        emp = Employee(name="Solo", firstname="Worker")
        session.add(emp)
        session.flush()
        assert emp.company_id is None

    def test_employee_linked_to_company(self, session):
        """Employees can be linked to a company via FK."""
        c = Company(name="LinkCo", slug="linkco")
        session.add(c)
        session.flush()

        emp = Employee(name="Linked", firstname="Worker", company_id=c.id)
        session.add(emp)
        session.flush()

        assert emp.company_id == c.id
        assert emp.company.name == "LinkCo"
        assert len(c.employees) == 1

    def test_companies_table_exists(self, engine):
        inspector = inspect(engine)
        assert "companies" in inspector.get_table_names()


class TestMigrationScript:
    """Test the migration script logic."""

    @pytest.fixture(autouse=True)
    def _import_migrate(self):
        """Make the migration function importable."""
        import sys
        from pathlib import Path

        scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

    def test_migration_creates_default_company(self, engine):
        """Running the migration on a fresh DB should create the default company."""
        from migrate_add_company import migrate

        # Tables already exist from init_db; migration inserts default + backfills.
        migrate(engine)

        sess = get_session(engine)
        default = sess.get(Company, 1)
        assert default is not None
        assert default.name == "Default"
        assert default.slug == "default"
        sess.close()

    def test_migration_backfills_employees(self, engine):
        """Employees created before migration should get company_id=1."""
        sess = get_session(engine)
        emp = Employee(name="Pre-Migration", firstname="Worker")
        sess.add(emp)
        sess.commit()
        assert emp.company_id is None
        sess.close()

        from migrate_add_company import migrate

        migrate(engine)

        sess2 = get_session(engine)
        emp_after = sess2.get(Employee, emp.id)
        assert emp_after.company_id == 1
        sess2.close()

    def test_migration_idempotent(self, engine):
        """Running migration twice should not fail."""
        from migrate_add_company import migrate

        migrate(engine)
        migrate(engine)  # second run should be fine

        sess = get_session(engine)
        companies = sess.query(Company).all()
        assert len(companies) == 1
        sess.close()
