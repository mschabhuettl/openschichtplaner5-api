"""
Tests for Company CRUD API and tenant isolation (Q044 phase 2).
"""

import os
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sp5lib.orm import get_engine, init_db  # noqa: E402, I001
from sp5lib.orm.base import get_session  # noqa: E402
from sp5lib.orm.models import Company, Employee, Group  # noqa: E402


# ── ORM-level tenant isolation tests ────────────────────────


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


class TestCompanyCRUD:
    """ORM-level Company CRUD."""

    def test_create_company(self, session):
        c = Company(name="Test Corp", slug="test-corp")
        session.add(c)
        session.flush()
        assert c.id is not None
        assert c.is_active is True

    def test_update_company(self, session):
        c = Company(name="Old Name", slug="old")
        session.add(c)
        session.flush()
        c.name = "New Name"
        session.flush()
        assert c.name == "New Name"

    def test_deactivate_company(self, session):
        c = Company(name="Deactivate Me", slug="deactivate")
        session.add(c)
        session.flush()
        c.is_active = False
        session.flush()
        assert c.is_active is False

    def test_company_employee_relationship(self, session):
        c = Company(name="Emps Corp", slug="emps")
        session.add(c)
        session.flush()

        e1 = Employee(name="Worker", firstname="A", company_id=c.id)
        e2 = Employee(name="Worker", firstname="B", company_id=c.id)
        session.add_all([e1, e2])
        session.flush()

        assert len(c.employees) == 2

    def test_company_group_relationship(self, session):
        c = Company(name="Groups Corp", slug="groups")
        session.add(c)
        session.flush()

        g = Group(name="Team A", company_id=c.id)
        session.add(g)
        session.flush()

        assert len(c.groups) == 1
        assert g.company.name == "Groups Corp"


class TestTenantIsolation:
    """Verify that tenant filtering works at the ORM level."""

    def test_employees_filtered_by_company(self, session):
        """User A's employees should not be visible to Company B."""
        co_a = Company(name="Company A", slug="co-a")
        co_b = Company(name="Company B", slug="co-b")
        session.add_all([co_a, co_b])
        session.flush()

        emp_a = Employee(name="Alice", firstname="A", company_id=co_a.id)
        emp_b = Employee(name="Bob", firstname="B", company_id=co_b.id)
        session.add_all([emp_a, emp_b])
        session.flush()

        # Tenant A query
        tenant_a_employees = (
            session.query(Employee).filter(Employee.company_id == co_a.id).all()
        )
        assert len(tenant_a_employees) == 1
        assert tenant_a_employees[0].name == "Alice"

        # Tenant B query
        tenant_b_employees = (
            session.query(Employee).filter(Employee.company_id == co_b.id).all()
        )
        assert len(tenant_b_employees) == 1
        assert tenant_b_employees[0].name == "Bob"

    def test_groups_filtered_by_company(self, session):
        """Groups should be scoped to their company."""
        co_a = Company(name="Corp A", slug="corp-a")
        co_b = Company(name="Corp B", slug="corp-b")
        session.add_all([co_a, co_b])
        session.flush()

        g_a = Group(name="Team Alpha", company_id=co_a.id)
        g_b = Group(name="Team Beta", company_id=co_b.id)
        session.add_all([g_a, g_b])
        session.flush()

        groups_a = session.query(Group).filter(Group.company_id == co_a.id).all()
        assert len(groups_a) == 1
        assert groups_a[0].name == "Team Alpha"

        groups_b = session.query(Group).filter(Group.company_id == co_b.id).all()
        assert len(groups_b) == 1
        assert groups_b[0].name == "Team Beta"

    def test_super_admin_sees_all(self, session):
        """A super-admin (no company filter) should see everything."""
        co_a = Company(name="SA Corp A", slug="sa-a")
        co_b = Company(name="SA Corp B", slug="sa-b")
        session.add_all([co_a, co_b])
        session.flush()

        emp_a = Employee(name="SA Alice", company_id=co_a.id)
        emp_b = Employee(name="SA Bob", company_id=co_b.id)
        session.add_all([emp_a, emp_b])
        session.flush()

        # No tenant filter = super-admin view
        all_emps = session.query(Employee).all()
        assert len(all_emps) == 2

    def test_cross_company_data_invisible(self, session):
        """Explicitly verify no cross-tenant leakage."""
        co_a = Company(name="Iso A", slug="iso-a")
        co_b = Company(name="Iso B", slug="iso-b")
        session.add_all([co_a, co_b])
        session.flush()

        # Create employees and groups for both companies
        for i in range(3):
            session.add(Employee(name=f"A-Emp-{i}", company_id=co_a.id))
            session.add(Employee(name=f"B-Emp-{i}", company_id=co_b.id))
            session.add(Group(name=f"A-Group-{i}", company_id=co_a.id))
            session.add(Group(name=f"B-Group-{i}", company_id=co_b.id))
        session.flush()

        # Company A perspective
        a_emps = session.query(Employee).filter(Employee.company_id == co_a.id).all()
        a_groups = session.query(Group).filter(Group.company_id == co_a.id).all()
        assert all("A-" in e.name for e in a_emps)
        assert all("A-" in g.name for g in a_groups)
        assert len(a_emps) == 3
        assert len(a_groups) == 3

        # Company B perspective
        b_emps = session.query(Employee).filter(Employee.company_id == co_b.id).all()
        b_groups = session.query(Group).filter(Group.company_id == co_b.id).all()
        assert all("B-" in e.name for e in b_emps)
        assert all("B-" in g.name for g in b_groups)
        assert len(b_emps) == 3
        assert len(b_groups) == 3


class TestMigrationPhase2:
    """Test the updated migration script with group backfill."""

    @pytest.fixture(autouse=True)
    def _import_migrate(self):
        import sys
        from pathlib import Path

        scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

    def test_migration_backfills_groups(self, engine):
        """Groups created before migration should get company_id=1."""
        sess = get_session(engine)
        grp = Group(name="Pre-Migration Group")
        sess.add(grp)
        sess.commit()
        assert grp.company_id is None
        sess.close()

        from migrate_add_company import migrate
        migrate(engine)

        sess2 = get_session(engine)
        grp_after = sess2.get(Group, grp.id)
        assert grp_after.company_id == 1
        sess2.close()


class TestCompanyAPIEndpoints:
    """Test the Company API router via the TestClient."""

    @pytest.fixture
    def admin_token(self):
        """Inject an admin session token."""
        import secrets

        from api.main import _sessions

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 901,
            "NAME": "test_admin",
            "role": "Admin",
            "ADMIN": True,
            "RIGHTS": 255,
            "company_id": None,  # super-admin
        }
        yield tok
        _sessions.pop(tok, None)

    @pytest.fixture
    def tenant_admin_token(self):
        """Inject a tenant-scoped admin session token."""
        import secrets

        from api.main import _sessions

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 902,
            "NAME": "tenant_admin",
            "role": "Admin",
            "ADMIN": True,
            "RIGHTS": 255,
            "company_id": 1,  # scoped to company 1
        }
        yield tok
        _sessions.pop(tok, None)

    @pytest.fixture
    def client(self, app):
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    @pytest.fixture
    def orm_engine(self, tmp_path):
        """Create a temp-file SQLite engine (survives across connections)."""
        from sp5lib.orm import get_engine, init_db
        db_file = str(tmp_path / "test_company.db")
        engine = get_engine(f"sqlite:///{db_file}")
        init_db(engine)
        return engine

    def test_create_company(self, client, admin_token, orm_engine):
        """POST /api/companies should create a new company."""
        from sp5lib.orm.base import get_session as orm_get_session

        def mock_get_orm_session():
            return orm_get_session(orm_engine), orm_engine

        with patch("api.routers.companies._get_orm_session", mock_get_orm_session):
            resp = client.post(
                "/api/companies",
                json={"name": "New Corp"},
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "New Corp"
            assert data["slug"] == "new-corp"
            assert data["is_active"] is True

    def test_list_companies(self, client, admin_token, orm_engine):
        """GET /api/companies should return all companies."""
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        # Seed data
        sess = orm_get_session(orm_engine)
        sess.add(Company(name="Corp A", slug="corp-a"))
        sess.add(Company(name="Corp B", slug="corp-b"))
        sess.commit()
        sess.close()

        def mock_get_orm_session():
            return orm_get_session(orm_engine), orm_engine

        with patch("api.routers.companies._get_orm_session", mock_get_orm_session):
            resp = client.get(
                "/api/companies",
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2

    def test_update_company(self, client, admin_token, orm_engine):
        """PUT /api/companies/{id} should update company."""
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        c = Company(name="Old", slug="old")
        sess.add(c)
        sess.commit()
        cid = c.id
        sess.close()

        def mock_get_orm_session():
            return orm_get_session(orm_engine), orm_engine

        with patch("api.routers.companies._get_orm_session", mock_get_orm_session):
            resp = client.put(
                f"/api/companies/{cid}",
                json={"name": "Updated Corp"},
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "Updated Corp"

    def test_delete_default_company_fails(self, client, admin_token):
        """DELETE /api/companies/1 should fail — default company is protected."""
        resp = client.delete(
            "/api/companies/1",
            headers={"X-Auth-Token": admin_token},
        )
        assert resp.status_code == 400

    def test_duplicate_company_name(self, client, admin_token, orm_engine):
        """POST with duplicate name should return 409."""
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        sess.add(Company(name="Existing", slug="existing"))
        sess.commit()
        sess.close()

        def mock_get_orm_session():
            return orm_get_session(orm_engine), orm_engine

        with patch("api.routers.companies._get_orm_session", mock_get_orm_session):
            resp = client.post(
                "/api/companies",
                json={"name": "Existing"},
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 409

    def test_unauthenticated_access_denied(self, client):
        """Unauthenticated requests should be rejected."""
        resp = client.get("/api/companies")
        assert resp.status_code == 401
