"""Tests for the extended /api/health endpoint (Q053)."""


class TestHealthExtended:
    """Extended health endpoint tests."""

    def test_health_returns_all_sections(self, sync_client):
        """Health response must contain all required top-level keys."""
        resp = sync_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        for key in (
            "status",
            "checks",
            "version",
            "uptime",
            "uptime_seconds",
            "started_at",
            "db",
            "disk",
            "memory",
            "sessions",
        ):
            assert key in data, f"Missing key: {key}"

    def test_health_checks_aggregation(self, sync_client):
        """Checks dict must contain db, disk, memory keys."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        checks = data["checks"]
        assert "db" in checks
        assert "disk" in checks
        assert "memory" in checks
        for v in checks.values():
            assert v in ("ok", "warning", "error")

    def test_health_status_matches_checks(self, sync_client):
        """Overall status must be consistent with individual checks."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        checks = data["checks"]
        values = list(checks.values())
        if "error" in values:
            assert data["status"] == "unhealthy"
        elif "warning" in values:
            assert data["status"] == "degraded"
        else:
            assert data["status"] == "healthy"

    def test_health_uptime_format(self, sync_client):
        """Uptime must be a human-readable string and seconds a number."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        assert isinstance(data["uptime"], str)
        assert len(data["uptime"]) > 0
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_health_disk_info(self, sync_client):
        """Disk section must report free space and usage."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        disk = data["disk"]
        assert "free_mb" in disk
        assert "total_mb" in disk
        assert "used_percent" in disk
        assert disk["free_mb"] >= 0
        assert 0 <= disk["used_percent"] <= 100

    def test_health_memory_info(self, sync_client):
        """Memory section must report process RSS."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        mem = data["memory"]
        assert "rss_mb" in mem
        assert mem["rss_mb"] > 0
        assert "system_used_percent" in mem

    def test_health_db_last_modified(self, sync_client):
        """DB section should include last_modified timestamp."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        db = data["db"]
        if db.get("dbf_ok", 0) > 0:
            assert "last_modified" in db
            assert "T" in db["last_modified"]

    def test_health_no_path_leak(self, sync_client):
        """Extended health must not leak file system paths."""
        resp = sync_client.get("/api/health")
        text = resp.text.lower()
        assert "/home/" not in text
        assert "/tmp/" not in text
        assert "c:\\" not in text

    def test_health_version_present(self, sync_client):
        """Version must be present and non-empty."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        assert data["version"]
        assert isinstance(data["version"], str)

    def test_health_started_at_iso(self, sync_client):
        """started_at must be a valid ISO timestamp."""
        resp = sync_client.get("/api/health")
        data = resp.json()
        assert "started_at" in data
        assert "T" in data["started_at"]
