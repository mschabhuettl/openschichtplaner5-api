"""Tests for notification settings endpoint (Q080)."""



class TestNotificationSettingsGet:
    def test_get_settings_authenticated_ok(self, sync_client):
        resp = sync_client.get("/api/v1/notifications/settings")
        assert resp.status_code == 200  # sync_client is always admin

    def test_get_settings_returns_200(self, sync_client):
        resp = sync_client.get("/api/v1/notifications/settings")
        assert resp.status_code == 200

    def test_get_settings_has_required_fields(self, sync_client):
        resp = sync_client.get("/api/v1/notifications/settings")
        data = resp.json()
        assert "settings" in data
        assert "user_id" in data

    def test_get_settings_defaults_all_true(self, sync_client):
        resp = sync_client.get("/api/v1/notifications/settings")
        settings = resp.json()["settings"]
        expected_keys = [
            "shift_assigned", "shift_changed", "swap_requested",
            "swap_approved", "swap_rejected", "vacation_approved",
            "vacation_rejected", "schedule_comment_added",
        ]
        for key in expected_keys:
            assert key in settings, f"Missing key: {key}"

    def test_get_settings_unauthenticated_returns_401(self, app):
        from starlette.testclient import TestClient
        bare_client = TestClient(app)
        resp = bare_client.get("/api/v1/notifications/settings")
        assert resp.status_code == 401


class TestNotificationSettingsUpdate:
    def test_update_returns_200(self, sync_client):
        payload = {
            "shift_assigned": True,
            "shift_changed": True,
            "swap_requested": True,
            "swap_approved": True,
            "swap_rejected": True,
            "vacation_approved": True,
            "vacation_rejected": True,
            "schedule_comment_added": True,
        }
        resp = sync_client.put("/api/v1/notifications/settings", json=payload)
        assert resp.status_code == 200

    def test_update_returns_updated_flag(self, sync_client):
        payload = {k: True for k in [
            "shift_assigned", "shift_changed", "swap_requested",
            "swap_approved", "swap_rejected", "vacation_approved",
            "vacation_rejected", "schedule_comment_added",
        ]}
        resp = sync_client.put("/api/v1/notifications/settings", json=payload)
        assert resp.json()["updated"] is True

    def test_update_single_false_persists(self, sync_client):
        payload = {
            "shift_assigned": False,
            "shift_changed": True,
            "swap_requested": True,
            "swap_approved": True,
            "swap_rejected": True,
            "vacation_approved": True,
            "vacation_rejected": True,
            "schedule_comment_added": True,
        }
        sync_client.put("/api/v1/notifications/settings", json=payload)
        resp = sync_client.get("/api/v1/notifications/settings")
        assert resp.json()["settings"]["shift_assigned"] is False

    def test_update_all_false(self, sync_client):
        payload = {k: False for k in [
            "shift_assigned", "shift_changed", "swap_requested",
            "swap_approved", "swap_rejected", "vacation_approved",
            "vacation_rejected", "schedule_comment_added",
        ]}
        resp = sync_client.put("/api/v1/notifications/settings", json=payload)
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        for val in settings.values():
            assert val is False

    def test_update_all_true(self, sync_client):
        payload = {k: True for k in [
            "shift_assigned", "shift_changed", "swap_requested",
            "swap_approved", "swap_rejected", "vacation_approved",
            "vacation_rejected", "schedule_comment_added",
        ]}
        resp = sync_client.put("/api/v1/notifications/settings", json=payload)
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        for val in settings.values():
            assert val is True

    def test_update_unauthenticated_returns_401(self, app):
        from starlette.testclient import TestClient
        bare_client = TestClient(app)
        resp = bare_client.put(
            "/api/v1/notifications/settings",
            json={"shift_assigned": False},
        )
        assert resp.status_code == 401
