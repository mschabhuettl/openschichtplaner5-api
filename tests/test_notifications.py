"""Tests for api/routers/notifications.py — coverage boost."""

import json

import pytest
from starlette.testclient import TestClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_clients(app, tmp_path, notif_file_path):
    """Return (admin_client, planer_client, employee_client) with fresh notif file."""
    import secrets

    from api.main import _sessions

    # Clear notifications
    with open(notif_file_path, "w") as f:
        json.dump([], f)

    admin_tok = secrets.token_hex(16)
    planer_tok = secrets.token_hex(16)
    emp_tok = secrets.token_hex(16)

    _sessions[admin_tok] = {
        "ID": 1,
        "NAME": "admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
    }
    _sessions[planer_tok] = {
        "ID": 2,
        "NAME": "planer",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    _sessions[emp_tok] = {
        "ID": 3,
        "NAME": "emp",
        "role": "Employee",
        "ADMIN": False,
        "RIGHTS": 1,
    }

    admin_c = TestClient(app, raise_server_exceptions=False)
    admin_c.headers["X-Auth-Token"] = admin_tok
    planer_c = TestClient(app, raise_server_exceptions=False)
    planer_c.headers["X-Auth-Token"] = planer_tok
    emp_c = TestClient(app, raise_server_exceptions=False)
    emp_c.headers["X-Auth-Token"] = emp_tok

    return admin_c, planer_c, emp_c, [admin_tok, planer_tok, emp_tok]


@pytest.fixture
def notif_env(app, tmp_path):
    """Fixture: patch _NOTIF_FILE to a temp file, return (admin_c, planer_c, emp_c, notif_path)."""
    import api.routers.notifications as notif_mod
    from api.main import _sessions

    notif_path = str(tmp_path / "notifications.json")
    orig = notif_mod._NOTIF_FILE
    notif_mod._NOTIF_FILE = notif_path

    with open(notif_path, "w") as f:
        json.dump([], f)

    admin_c, planer_c, emp_c, toks = _make_clients(app, tmp_path, notif_path)

    yield admin_c, planer_c, emp_c, notif_path

    notif_mod._NOTIF_FILE = orig
    for tok in toks:
        _sessions.pop(tok, None)


# ── create_notification helper ────────────────────────────────────────────────


class TestCreateNotification:
    def test_creates_notification(self, notif_env):
        _, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        entry = nm.create_notification(type="info", title="Test", message="Hello")
        assert entry["id"] == 1
        assert entry["read"] is False
        assert entry["type"] == "info"

    def test_creates_with_recipient(self, notif_env):
        _, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        entry = nm.create_notification(
            type="warn",
            title="Warn",
            message="msg",
            recipient_employee_id=42,
            link="/foo",
        )
        assert entry["recipient_employee_id"] == 42
        assert entry["link"] == "/foo"

    def test_id_increments(self, notif_env):
        _, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        e1 = nm.create_notification(type="a", title="a", message="a")
        e2 = nm.create_notification(type="b", title="b", message="b")
        assert e2["id"] == e1["id"] + 1


# ── GET /api/notifications ────────────────────────────────────────────────────


class TestListNotifications:
    def test_empty(self, notif_env):
        admin_c, _, _, _ = notif_env
        r = admin_c.get("/api/notifications")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_returns_planner_wide(self, notif_env):
        admin_c, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        nm.create_notification(type="info", title="T", message="M")
        r = admin_c.get("/api/notifications")
        assert r.json()["count"] == 1

    def test_filter_by_employee_id(self, notif_env):
        admin_c, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=5
        )
        nm.create_notification(type="info", title="T2", message="M2")
        r = admin_c.get("/api/notifications?employee_id=5")
        assert r.json()["count"] == 1

    def test_unread_only(self, notif_env):
        admin_c, _, _, notif_path = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(type="info", title="T", message="M")
        # mark read via patch
        admin_c.patch(f"/api/notifications/{e['id']}/read")
        r = admin_c.get("/api/notifications?unread_only=true")
        assert r.json()["count"] == 0

    def test_non_admin_cannot_see_other_employee_notifs(self, notif_env):
        _, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=99
        )
        # planer has ID=2, requesting employee_id=99 → 403
        r = planer_c.get("/api/notifications?employee_id=99")
        assert r.status_code == 403

    def test_non_admin_can_see_own_notifs(self, notif_env):
        _, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=2
        )
        r = planer_c.get("/api/notifications?employee_id=2")
        assert r.status_code == 200
        assert r.json()["count"] == 1


# ── GET /api/notifications/all ────────────────────────────────────────────────


class TestListAllNotifications:
    def test_admin_sees_all(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        nm.create_notification(type="info", title="T", message="M")
        nm.create_notification(
            type="info", title="T2", message="M2", recipient_employee_id=5
        )
        r = admin_c.get("/api/notifications/all")
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_non_admin_forbidden(self, notif_env):
        _, planer_c, _, _ = notif_env
        r = planer_c.get("/api/notifications/all")
        assert r.status_code == 403

    def test_unread_only_filter(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(type="info", title="T", message="M")
        admin_c.patch(f"/api/notifications/{e['id']}/read")
        r = admin_c.get("/api/notifications/all?unread_only=true")
        assert r.json()["count"] == 0


# ── PATCH /api/notifications/{id}/read ───────────────────────────────────────


class TestMarkRead:
    def test_mark_read(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(type="info", title="T", message="M")
        r = admin_c.patch(f"/api/notifications/{e['id']}/read")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_mark_read_not_found(self, notif_env):
        admin_c, _, _, _ = notif_env
        r = admin_c.patch("/api/notifications/99999/read")
        assert r.status_code == 404

    def test_non_admin_cannot_mark_others(self, notif_env):
        admin_c, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        # notification for employee 99, planer has ID=2
        e = nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=99
        )
        r = planer_c.patch(f"/api/notifications/{e['id']}/read")
        assert r.status_code == 403

    def test_non_admin_can_mark_own(self, notif_env):
        admin_c, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=2
        )
        r = planer_c.patch(f"/api/notifications/{e['id']}/read")
        assert r.status_code == 200


# ── PATCH /api/notifications/read-all ─────────────────────────────────────────


class TestMarkAllRead:
    def test_mark_all_planner_wide(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        nm.create_notification(type="info", title="T1", message="M")
        nm.create_notification(type="info", title="T2", message="M")
        r = admin_c.patch("/api/notifications/read-all")
        assert r.status_code == 200
        assert r.json()["marked"] == 2

    def test_mark_all_by_employee(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=5
        )
        nm.create_notification(
            type="info", title="T2", message="M2", recipient_employee_id=5
        )
        r = admin_c.patch("/api/notifications/read-all?employee_id=5")
        assert r.json()["marked"] == 2

    def test_already_read_not_counted(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(type="info", title="T", message="M")
        admin_c.patch(f"/api/notifications/{e['id']}/read")
        r = admin_c.patch("/api/notifications/read-all")
        assert r.json()["marked"] == 0


# ── DELETE /api/notifications/{id} ───────────────────────────────────────────


class TestDeleteNotification:
    def test_delete_own(self, notif_env):
        admin_c, _, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(type="info", title="T", message="M")
        r = admin_c.delete(f"/api/notifications/{e['id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_not_found(self, notif_env):
        admin_c, _, _, _ = notif_env
        r = admin_c.delete("/api/notifications/99999")
        assert r.status_code == 404

    def test_non_admin_cannot_delete_others(self, notif_env):
        _, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=99
        )
        r = planer_c.delete(f"/api/notifications/{e['id']}")
        assert r.status_code == 403

    def test_non_admin_can_delete_own(self, notif_env):
        _, planer_c, _, _ = notif_env
        import api.routers.notifications as nm

        e = nm.create_notification(
            type="info", title="T", message="M", recipient_employee_id=2
        )
        r = planer_c.delete(f"/api/notifications/{e['id']}")
        assert r.status_code == 200
