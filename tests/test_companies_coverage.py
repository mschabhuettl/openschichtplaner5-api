"""Coverage boost for companies router — targets uncovered lines (60% → 80%+).

Tests the API router endpoints with mocked ORM sessions, covering:
  - _slugify helper
  - get_company endpoint
  - delete_company endpoint
  - tenant-scoped admin access
  - error paths (404s, 403s)
"""

import os
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class TestSlugify:
    """Unit tests for _slugify helper."""

    def test_basic_slug(self):
        from api.routers.companies import _slugify
        assert _slugify("Test Corp") == "test-corp"

    def test_special_characters(self):
        from api.routers.companies import _slugify
        assert _slugify("Böse & Söhne GmbH") == "bse-shne-gmbh"

    def test_empty_name_returns_company(self):
        from api.routers.companies import _slugify
        assert _slugify("!!!") == "company"

    def test_whitespace_and_dashes(self):
        from api.routers.companies import _slugify
        assert _slugify("  My  Cool  Company  ") == "my-cool-company"

    def test_already_slug(self):
        from api.routers.companies import _slugify
        assert _slugify("already-a-slug") == "already-a-slug"


class TestIsSuperAdmin:
    """Tests for _is_super_admin."""

    def test_super_admin(self):
        from api.routers.companies import _is_super_admin
        assert _is_super_admin({"role": "Admin", "company_id": None}) is True

    def test_tenant_admin(self):
        from api.routers.companies import _is_super_admin
        assert _is_super_admin({"role": "Admin", "company_id": 1}) is False

    def test_non_admin(self):
        from api.routers.companies import _is_super_admin
        assert _is_super_admin({"role": "Planer", "company_id": None}) is False


class TestCompanyEndpoints:
    """API-level tests for missing coverage paths."""

    @pytest.fixture
    def orm_engine(self, tmp_path):
        from sp5lib.orm import get_engine, init_db
        db_file = str(tmp_path / "test_co.db")
        engine = get_engine(f"sqlite:///{db_file}")
        init_db(engine)
        return engine

    @pytest.fixture
    def admin_token(self):
        import secrets

        from api.main import _sessions
        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 950, "NAME": "co_test_admin", "role": "Admin",
            "ADMIN": True, "RIGHTS": 255, "company_id": None,
        }
        yield tok
        _sessions.pop(tok, None)

    @pytest.fixture
    def tenant_token(self):
        import secrets

        from api.main import _sessions
        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 951, "NAME": "co_tenant_admin", "role": "Admin",
            "ADMIN": True, "RIGHTS": 255, "company_id": 1,
        }
        yield tok
        _sessions.pop(tok, None)

    @pytest.fixture
    def client(self, app):
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def _mock_orm(self, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        def mock():
            return orm_get_session(orm_engine), orm_engine
        return mock

    def test_get_company_success(self, client, admin_token, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        c = Company(name="Get Test", slug="get-test")
        sess.add(c)
        sess.commit()
        cid = c.id
        sess.close()

        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.get(f"/api/companies/{cid}", headers={"X-Auth-Token": admin_token})
            assert resp.status_code == 200
            assert resp.json()["name"] == "Get Test"

    def test_get_company_not_found(self, client, admin_token, orm_engine):
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.get("/api/companies/9999", headers={"X-Auth-Token": admin_token})
            assert resp.status_code == 404

    def test_get_company_tenant_forbidden(self, client, tenant_token, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        # Create two companies; tenant_token is scoped to company_id=1
        c1 = Company(name="My Co", slug="my-co")
        sess.add(c1)
        sess.flush()
        c2 = Company(name="Other Co", slug="other-co")
        sess.add(c2)
        sess.commit()
        other_id = c2.id
        sess.close()

        # tenant_token is scoped to company_id=1, accessing other company should be 403
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.get(f"/api/companies/{other_id}", headers={"X-Auth-Token": tenant_token})
            assert resp.status_code == 403

    def test_delete_company_success(self, client, admin_token, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        # Create a dummy first company so our target gets id > 1
        c1 = Company(name="Default", slug="default")
        sess.add(c1)
        sess.flush()
        c = Company(name="Delete Me", slug="delete-me")
        sess.add(c)
        sess.commit()
        cid = c.id
        sess.close()

        assert cid > 1, "Target must not be id=1 (protected)"
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.delete(f"/api/companies/{cid}", headers={"X-Auth-Token": admin_token})
            assert resp.status_code == 200
            assert resp.json()["deactivated"] == cid

    def test_delete_company_not_found(self, client, admin_token, orm_engine):
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.delete("/api/companies/9999", headers={"X-Auth-Token": admin_token})
            assert resp.status_code == 404

    def test_update_company_not_found(self, client, admin_token, orm_engine):
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.put("/api/companies/9999", json={"name": "X"}, headers={"X-Auth-Token": admin_token})
            assert resp.status_code == 404

    def test_list_companies_tenant_scoped(self, client, tenant_token, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        c1 = Company(id=1, name="My Co", slug="my-co")
        c2 = Company(name="Other Co", slug="other-co")
        sess.add_all([c1, c2])
        sess.commit()
        sess.close()

        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.get("/api/companies", headers={"X-Auth-Token": tenant_token})
            assert resp.status_code == 200
            data = resp.json()
            # tenant_token company_id=1, should only see company 1
            assert len(data) == 1
            assert data[0]["id"] == 1

    def test_create_company_with_custom_slug(self, client, admin_token, orm_engine):
        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.post(
                "/api/companies",
                json={"name": "Custom Slug Co", "slug": "custom-slug"},
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 201
            assert resp.json()["slug"] == "custom-slug"

    def test_update_company_all_fields(self, client, admin_token, orm_engine):
        from sp5lib.orm.base import get_session as orm_get_session
        from sp5lib.orm.models import Company

        sess = orm_get_session(orm_engine)
        c = Company(name="Update All", slug="update-all")
        sess.add(c)
        sess.commit()
        cid = c.id
        sess.close()

        with patch("api.routers.companies._get_orm_session", self._mock_orm(orm_engine)):
            resp = client.put(
                f"/api/companies/{cid}",
                json={"name": "New Name", "slug": "new-slug", "is_active": False},
                headers={"X-Auth-Token": admin_token},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "New Name"
            assert data["slug"] == "new-slug"
            assert data["is_active"] is False
