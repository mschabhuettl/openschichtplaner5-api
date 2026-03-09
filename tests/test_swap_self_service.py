"""Tests for self-service swap request endpoints (Q006)."""
import secrets

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def _employee_ids(write_db_path):
    """Return two valid employee IDs from the test DB."""
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    emps = db.get_employees(include_hidden=False)
    assert len(emps) >= 2, "Need at least 2 employees in test DB"
    return emps[0]["ID"], emps[1]["ID"], emps[0].get("NAME", "emp0"), emps[1].get("NAME", "emp1")


def _make_client(app, role, name):
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 800 + hash(name) % 100,
        "NAME": name,
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else 1,
    }
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-Auth-Token"] = tok
    c._test_token = tok
    return c, tok


class TestSelfServiceSwapRequests:
    """Test the full self-service swap lifecycle."""

    def test_create_self_swap_request(self, app, _employee_ids):
        """Employee can create a swap request (status=pending_partner)."""
        emp_a_id, emp_b_id, emp_a_name, _emp_b_name = _employee_ids
        client, tok = _make_client(app, "Leser", emp_a_name)
        try:
            res = client.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-06-01",
                "partner_date": "2025-06-02",
                "note": "Bitte tauschen",
            })
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["status"] == "pending_partner"
            assert data["requester_id"] == emp_a_id
            assert data["partner_id"] == emp_b_id
            assert data["partner_accepted"] is None
        finally:
            from api.main import _sessions
            _sessions.pop(tok, None)

    def test_partner_accepts(self, app, _employee_ids):
        """Partner can accept a pending_partner request → becomes pending."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        # Create as employee A
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-07-01",
                "partner_date": "2025-07-02",
            })
            assert res.status_code == 200
            swap_id = res.json()["id"]
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a, None)

        # Accept as employee B
        client_b, tok_b = _make_client(app, "Leser", emp_b_name)
        try:
            res = client_b.patch(f"/api/self/swap-requests/{swap_id}/respond", json={"accept": True})
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["status"] == "pending"
            assert data["partner_accepted"] is True
        finally:
            _sessions.pop(tok_b, None)

    def test_partner_declines(self, app, _employee_ids):
        """Partner can decline → status becomes rejected."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-08-01",
                "partner_date": "2025-08-02",
            })
            swap_id = res.json()["id"]
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a, None)

        client_b, tok_b = _make_client(app, "Leser", emp_b_name)
        try:
            res = client_b.patch(f"/api/self/swap-requests/{swap_id}/respond", json={"accept": False})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "rejected"
            assert data["partner_accepted"] is False
            assert "Partner" in data.get("reject_reason", "")
        finally:
            from api.main import _sessions
            _sessions.pop(tok_b, None)

    def test_wrong_partner_cannot_respond(self, app, _employee_ids):
        """Non-partner employee cannot respond."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-09-01",
                "partner_date": "2025-09-02",
            })
            swap_id = res.json()["id"]
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a, None)

        # Try to respond as requester (not the partner)
        client_a2, tok_a2 = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a2.patch(f"/api/self/swap-requests/{swap_id}/respond", json={"accept": True})
            assert res.status_code == 403
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a2, None)

    def test_self_cancel(self, app, _employee_ids):
        """Requester can cancel own request."""
        emp_a_id, emp_b_id, emp_a_name, _emp_b_name = _employee_ids
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-10-01",
                "partner_date": "2025-10-02",
            })
            swap_id = res.json()["id"]
            res = client_a.delete(f"/api/self/swap-requests/{swap_id}")
            assert res.status_code == 200
            assert res.json()["ok"] is True
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a, None)

    def test_cannot_swap_with_self(self, app, _employee_ids):
        """Cannot create swap with yourself."""
        emp_a_id, _emp_b_id, emp_a_name, _emp_b_name = _employee_ids
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_a_id,
                "requester_date": "2025-11-01",
                "partner_date": "2025-11-02",
            })
            assert res.status_code == 400
        finally:
            from api.main import _sessions
            _sessions.pop(tok_a, None)

    def test_full_lifecycle_with_planner_approval(self, app, _employee_ids):
        """Full flow: create → partner accept → planner approve."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        from api.main import _sessions

        # 1. Employee A creates request
        client_a, tok_a = _make_client(app, "Leser", emp_a_name)
        try:
            res = client_a.post("/api/self/swap-requests", json={
                "partner_id": emp_b_id,
                "requester_date": "2025-12-01",
                "partner_date": "2025-12-02",
                "note": "Full lifecycle test",
            })
            assert res.status_code == 200
            swap_id = res.json()["id"]
            assert res.json()["status"] == "pending_partner"
        finally:
            _sessions.pop(tok_a, None)

        # 2. Partner B accepts
        client_b, tok_b = _make_client(app, "Leser", emp_b_name)
        try:
            res = client_b.patch(f"/api/self/swap-requests/{swap_id}/respond", json={"accept": True})
            assert res.status_code == 200
            assert res.json()["status"] == "pending"
        finally:
            _sessions.pop(tok_b, None)

        # 3. Planner approves
        planner, tok_p = _make_client(app, "Planer", "test_planer")
        try:
            res = planner.patch(f"/api/swap-requests/{swap_id}/resolve", json={
                "action": "approve",
                "resolved_by": "test_planer",
            })
            assert res.status_code == 200
            assert res.json()["status"] == "approved"
        finally:
            _sessions.pop(tok_p, None)

    def test_resolve_sends_notifications(self, app, _employee_ids):
        """Planner resolve endpoint returns successfully (notifications fire-and-forget)."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        from api.main import _sessions

        planner, tok_p = _make_client(app, "Planer", "test_planer")
        try:
            # Create directly via planner
            res = planner.post("/api/swap-requests", json={
                "requester_id": emp_a_id,
                "requester_date": "2025-04-01",
                "partner_id": emp_b_id,
                "partner_date": "2025-04-02",
            })
            assert res.status_code == 200
            swap_id = res.json()["id"]

            # Reject — should succeed and internally create notifications
            res = planner.patch(f"/api/swap-requests/{swap_id}/resolve", json={
                "action": "reject",
                "reject_reason": "Test rejection",
            })
            assert res.status_code == 200
            assert res.json()["status"] == "rejected"
        finally:
            _sessions.pop(tok_p, None)
