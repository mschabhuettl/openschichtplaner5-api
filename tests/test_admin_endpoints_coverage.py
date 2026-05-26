"""Coverage for admin router endpoints: the accounting-period validator and
the sanitized-500 error handlers on the period/settings endpoints, plus the
rate-limit dashboard's summary computation."""

import api.routers.admin as admin


def _boom():
    raise RuntimeError("db down")


class TestPeriodAndSettingsErrors:
    def test_create_period_calendar_invalid_date(self, planer_client):
        # Matches the YYYY-MM-DD pattern but isn't a real date → validator → 422.
        resp = planer_client.post(
            "/api/periods",
            json={"group_id": 1, "start": "2026-13-45", "end": "2026-12-31"},
        )
        assert resp.status_code == 422

    def test_create_period_db_error_is_sanitized_500(self, planer_client, monkeypatch):
        monkeypatch.setattr(admin, "get_db", _boom)
        resp = planer_client.post(
            "/api/periods",
            json={"group_id": 1, "start": "2026-01-01", "end": "2026-12-31"},
        )
        assert resp.status_code == 500
        assert "db down" not in resp.text  # raw error not leaked

    def test_delete_period_db_error_is_sanitized_500(self, planer_client, monkeypatch):
        monkeypatch.setattr(admin, "get_db", _boom)
        resp = planer_client.delete("/api/periods/1")
        assert resp.status_code == 500

    def test_get_settings_db_error_is_sanitized_500(self, admin_client, monkeypatch):
        monkeypatch.setattr(admin, "get_db", _boom)
        resp = admin_client.get("/api/settings")
        assert resp.status_code == 500

    def test_update_settings_db_error_is_sanitized_500(self, admin_client, monkeypatch):
        monkeypatch.setattr(admin, "get_db", _boom)
        resp = admin_client.put("/api/settings", json={"BACKUPFR": 7})
        assert resp.status_code == 500


class TestRateLimitDashboard:
    def test_summary_aggregates_events(self, admin_client, monkeypatch):
        events = [
            {"user": "Alice", "endpoint": "/api/login", "ip": "1.1.1.1"},
            {"user": "Alice", "endpoint": "/api/login", "ip": "2.2.2.2"},
            {"user": None, "endpoint": "/api/x", "ip": "1.1.1.1"},
        ]
        monkeypatch.setattr("api.rate_limit_store.get_rate_limit_events", lambda **kw: events)
        resp = admin_client.get("/api/v1/admin/rate-limits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        top_users = {u["name"]: u["count"] for u in data["summary"]["top_users"]}
        assert top_users["Alice"] == 2
        assert top_users["(anonymous)"] == 1
        top_eps = {e["name"]: e["count"] for e in data["summary"]["top_endpoints"]}
        assert top_eps["/api/login"] == 2
        top_ips = {i["name"]: i["count"] for i in data["summary"]["top_ips"]}
        assert top_ips["1.1.1.1"] == 2
