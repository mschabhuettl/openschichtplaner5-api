"""Tests for GET /api/health's documented status contract (P2-7 / Punkte 36/37).

The System-Health-Dashboard reads `status` (healthy|degraded|unhealthy) and
`db.status` (ok|error). It also shows the employee count, which the health check
already computes via `get_stats()` — this test pins those response fields so the
dashboard's „Backend-Status" / „Datenbank" / „Mitarbeiter" stay correct.
"""


def test_health_reports_documented_status_values(sync_client):
    res = sync_client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    # Overall status is one of the documented values (the dashboard maps these).
    assert data["status"] in {"healthy", "degraded", "unhealthy"}
    # DB sub-status uses ok/error (NOT 'connected') — the dashboard checks == 'ok'.
    assert data["db"]["status"] in {"ok", "error"}


def test_health_includes_employee_count(sync_client):
    res = sync_client.get("/api/v1/health")
    assert res.status_code == 200
    db = res.json()["db"]
    # With a readable DB the health check exposes the employee count for the
    # dashboard KPI (previously get_stats() was computed and thrown away).
    if db["status"] == "ok":
        assert "employees" in db
        assert isinstance(db["employees"], int)


def test_health_does_not_leak_db_path(sync_client):
    # Public endpoint — never expose the filesystem path or error details.
    db = sync_client.get("/api/v1/health").json()["db"]
    assert "path" not in db
    assert "error" not in db
