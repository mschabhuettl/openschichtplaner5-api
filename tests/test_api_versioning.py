"""Tests for API versioning: /api/v1/ prefix, deprecation headers, X-API-Version."""

class TestApiVersioning:
    """Test /api/v1/ prefix routing and deprecation headers."""

    def test_v1_version_endpoint(self, sync_client):
        """GET /api/v1/version should work and return version info."""
        res = sync_client.get("/api/v1/version")
        assert res.status_code == 200
        data = res.json()
        assert "version" in data
        assert "service" in data

    def test_v1_health_endpoint(self, sync_client):
        """GET /api/v1/health should work."""
        res = sync_client.get("/api/v1/health")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data

    def test_v1_employees_endpoint(self, sync_client):
        """GET /api/v1/employees should work (authenticated)."""
        res = sync_client.get("/api/v1/employees")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_v1_groups_endpoint(self, sync_client):
        """GET /api/v1/groups should work (authenticated)."""
        res = sync_client.get("/api/v1/groups")
        assert res.status_code == 200

    def test_v1_shifts_endpoint(self, sync_client):
        """GET /api/v1/shifts should work (authenticated)."""
        res = sync_client.get("/api/v1/shifts")
        assert res.status_code == 200

    def test_v1_stats_endpoint(self, sync_client):
        """GET /api/v1/stats should work (authenticated)."""
        res = sync_client.get("/api/v1/stats")
        assert res.status_code == 200

    def test_v1_api_root(self, sync_client):
        """GET /api/v1 should map to /api root."""
        res = sync_client.get("/api/v1")
        assert res.status_code == 200
        data = res.json()
        assert "service" in data

    def test_unversioned_still_works(self, sync_client):
        """GET /api/version should still work for backward compat."""
        res = sync_client.get("/api/version")
        assert res.status_code == 200
        data = res.json()
        assert "version" in data

    def test_unversioned_employees_still_works(self, sync_client):
        """GET /api/employees should still work."""
        res = sync_client.get("/api/employees")
        assert res.status_code == 200


class TestDeprecationHeaders:
    """Test that unversioned /api/ routes get deprecation headers."""

    def test_deprecation_header_on_unversioned(self, sync_client):
        """Unversioned /api/version should have Deprecation: true header."""
        res = sync_client.get("/api/version")
        assert res.headers.get("Deprecation") == "true"

    def test_sunset_header_on_unversioned(self, sync_client):
        """Unversioned /api/version should have Sunset header."""
        res = sync_client.get("/api/version")
        assert "Sunset" in res.headers

    def test_link_header_on_unversioned(self, sync_client):
        """Unversioned routes should have Link header pointing to v1."""
        res = sync_client.get("/api/version")
        link = res.headers.get("Link", "")
        assert "/api/v1/version" in link
        assert 'rel="successor-version"' in link

    def test_no_deprecation_on_versioned(self, sync_client):
        """Versioned /api/v1/version should NOT have Deprecation header."""
        res = sync_client.get("/api/v1/version")
        assert res.headers.get("Deprecation") is None

    def test_no_sunset_on_versioned(self, sync_client):
        """Versioned /api/v1/version should NOT have Sunset header."""
        res = sync_client.get("/api/v1/version")
        assert "Sunset" not in res.headers

    def test_deprecation_on_authenticated_endpoint(self, sync_client):
        """Unversioned /api/employees should have deprecation headers."""
        res = sync_client.get("/api/employees")
        assert res.headers.get("Deprecation") == "true"
        assert "Sunset" in res.headers

    def test_no_deprecation_on_v1_authenticated(self, sync_client):
        """Versioned /api/v1/employees should NOT have deprecation headers."""
        res = sync_client.get("/api/v1/employees")
        assert res.headers.get("Deprecation") is None


class TestXApiVersionHeader:
    """Test that X-API-Version: 1 is present on all responses."""

    def test_x_api_version_on_public_endpoint(self, sync_client):
        """X-API-Version should be '1' on /api/version."""
        res = sync_client.get("/api/version")
        assert res.headers.get("X-API-Version") == "1"

    def test_x_api_version_on_v1_endpoint(self, sync_client):
        """X-API-Version should be '1' on /api/v1/version."""
        res = sync_client.get("/api/v1/version")
        assert res.headers.get("X-API-Version") == "1"

    def test_x_api_version_on_authenticated_endpoint(self, sync_client):
        """X-API-Version should be '1' on /api/employees."""
        res = sync_client.get("/api/employees")
        assert res.headers.get("X-API-Version") == "1"

    def test_x_api_version_on_health(self, sync_client):
        """X-API-Version should be '1' on /api/health."""
        res = sync_client.get("/api/health")
        assert res.headers.get("X-API-Version") == "1"
