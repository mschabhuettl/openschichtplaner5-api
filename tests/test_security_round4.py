"""Security Round 4 tests — new endpoints audit."""


class TestHealthEndpoint:
    """Public /api/health must not leak sensitive info."""

    def test_health_ok(self, sync_client):
        """Verify health ok."""
        resp = sync_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_no_db_path(self, sync_client):
        """DB path must not appear in public health response."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        db = data.get("db", {})
        assert "path" not in db, "DB path must not be exposed in public health endpoint"

    def test_health_no_recent_errors(self, sync_client):
        """Recent log errors must not appear in public health response."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        assert "recent_errors" not in data, "Log errors must not be exposed publicly"

    def test_health_no_cache_details(self, sync_client):
        """Cache details must not appear in public health response."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        assert "cache" not in data, "Cache info must not be exposed publicly"

    def test_api_root_no_db_path(self, sync_client):
        """API root must not expose db_path."""
        resp = sync_client.get("/api")
        data = resp.json()
        assert "db_path" not in data, "db_path must not be in public API root"


class TestFrontendErrorEndpoint:
    """POST /api/errors — public but rate-limited and validated."""

    def test_report_error_ok(self, sync_client):
        """Verify report error ok."""
        resp = sync_client.post("/api/errors", json={
            "error": "Test error",
            "url": "http://localhost/",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_report_error_field_too_long(self, sync_client):
        """Error field must be capped at 2000 chars."""
        resp = sync_client.post("/api/errors", json={
            "error": "x" * 2001,
        })
        assert resp.status_code == 422

    def test_report_error_component_stack_too_long(self, sync_client):
        """Verify report error component stack too long."""
        resp = sync_client.post("/api/errors", json={
            "error": "err",
            "component_stack": "x" * 5001,
        })
        assert resp.status_code == 422

    def test_admin_frontend_errors_accessible_as_admin(self, sync_client):
        """sync_client is admin-authenticated — should succeed."""
        resp = sync_client.get("/api/admin/frontend-errors")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "errors" in data


class TestAdminCacheStats:
    """GET /api/admin/cache-stats — admin only."""

    def test_accessible_as_admin(self, sync_client):
        """Verify accessible as admin."""
        resp = sync_client.get("/api/admin/cache-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "cache" in data


class TestUnauthenticated:
    """Endpoints that must reject unauthenticated access."""

    def test_admin_frontend_errors_requires_auth(self, app):
        """GET /api/admin/frontend-errors must return 401 without token."""
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/admin/frontend-errors")
        assert resp.status_code == 401

    def test_admin_cache_stats_requires_auth(self, app):
        """GET /api/admin/cache-stats must return 401 without token."""
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/admin/cache-stats")
        assert resp.status_code == 401

    def test_sse_requires_auth(self, app):
        """GET /api/events must return 401 without token."""
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/events", headers={"Accept": "text/event-stream"})
        assert resp.status_code == 401
