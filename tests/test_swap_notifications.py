"""Tests for Q072 — Schicht-Tausch-Genehmigung verbessern.

Covers:
- Status history tracking (creation, partner response, planner resolve, cancellation)
- Email notifications (SMTP not configured → log, not fail)
- Rejection reason stored in history
- Auto-expiry of old swap requests
- GET /api/v1/shifts/swap/{id}/history endpoint
- POST /api/v1/shifts/swap/expire endpoint
"""
import secrets
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

# ─── helpers ────────────────────────────────────────────────────────────────


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
        "ID": 900 + hash(name) % 100,
        "NAME": name,
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else 1,
    }
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-Auth-Token"] = tok
    c._test_token = tok
    return c, tok


def _planner(app):
    return _make_client(app, "Planer", "test_planer_q072")


# ─── Status history tests ────────────────────────────────────────────────────


class TestStatusHistory:
    def test_create_request_initialises_history(self, write_db_path):
        """New swap request has exactly one history entry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-06-01",
            partner_id=2,
            partner_date="2025-06-02",
            created_by="test_user",
        )
        assert "status_history" in entry
        assert len(entry["status_history"]) == 1
        h = entry["status_history"][0]
        assert h["status"] == "pending"
        assert h["changed_by"] == "test_user"
        assert h["changed_at"]  # non-empty timestamp

    def test_partner_accept_adds_history(self, write_db_path):
        """Partner accepting a swap adds a history entry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-06-03",
            partner_id=2,
            partner_date="2025-06-04",
            status="pending_partner",
        )
        result = db.partner_respond_swap(entry["id"], accept=True, partner_name="emp_b")
        assert result["status"] == "pending"
        history = db.get_swap_request_history(entry["id"])
        statuses = [h["status"] for h in history]
        assert "pending_partner" in statuses
        assert "pending" in statuses

    def test_partner_decline_adds_history_with_reason(self, write_db_path):
        """Partner declining adds history with reason."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-07-01",
            partner_id=2,
            partner_date="2025-07-02",
            status="pending_partner",
        )
        result = db.partner_respond_swap(entry["id"], accept=False, partner_name="emp_b")
        assert result["status"] == "rejected"
        history = db.get_swap_request_history(entry["id"])
        rejected_entries = [h for h in history if h["status"] == "rejected"]
        assert len(rejected_entries) == 1
        assert "abgelehnt" in rejected_entries[0]["reason"].lower()

    def test_planner_approve_adds_history(self, write_db_path):
        """Planner approving adds approved entry to history."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-08-01",
            partner_id=2,
            partner_date="2025-08-02",
        )
        result = db.resolve_swap_request(entry["id"], "approve", resolved_by="planer1")
        assert result["status"] == "approved"
        history = db.get_swap_request_history(entry["id"])
        approved = [h for h in history if h["status"] == "approved"]
        assert len(approved) == 1
        assert approved[0]["changed_by"] == "planer1"

    def test_planner_reject_stores_reason_in_history(self, write_db_path):
        """Rejection reason is stored in the history entry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-09-01",
            partner_id=2,
            partner_date="2025-09-02",
        )
        result = db.resolve_swap_request(
            entry["id"], "reject", resolved_by="planer1", reject_reason="Kein Personal"
        )
        assert result["status"] == "rejected"
        history = db.get_swap_request_history(entry["id"])
        rejected = [h for h in history if h["status"] == "rejected"]
        assert len(rejected) == 1
        assert "Kein Personal" in rejected[0]["reason"]

    def test_cancel_adds_history(self, write_db_path):
        """Cancelling a swap request adds cancelled entry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-10-01",
            partner_id=2,
            partner_date="2025-10-02",
            status="pending_partner",
        )
        db.cancel_swap_request(entry["id"], cancelled_by="emp_a")
        history = db.get_swap_request_history(entry["id"])
        cancelled = [h for h in history if h["status"] == "cancelled"]
        assert len(cancelled) == 1
        assert cancelled[0]["changed_by"] == "emp_a"

    def test_get_swap_request_history_not_found(self, write_db_path):
        """Returns None for unknown swap ID."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        result = db.get_swap_request_history(99999)
        assert result is None


# ─── Auto-expiry tests ────────────────────────────────────────────────────────


class TestAutoExpiry:
    def test_expire_old_pending_request(self, write_db_path):
        """Swap older than 7 days in pending status gets expired."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-01-01",
            partner_id=2,
            partner_date="2025-01-02",
        )
        # Manually backdate created_at
        entries = db._load_swap_requests()
        for e in entries:
            if e["id"] == entry["id"]:
                e["created_at"] = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        db._save_swap_requests(entries)

        expired = db.expire_old_swap_requests(max_age_days=7)
        assert entry["id"] in expired

        history = db.get_swap_request_history(entry["id"])
        assert any(h["status"] == "expired" for h in history)

    def test_expire_old_pending_partner_request(self, write_db_path):
        """Swap older than 7 days in pending_partner status also gets expired."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-01-03",
            partner_id=2,
            partner_date="2025-01-04",
            status="pending_partner",
        )
        entries = db._load_swap_requests()
        for e in entries:
            if e["id"] == entry["id"]:
                e["created_at"] = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
        db._save_swap_requests(entries)

        expired = db.expire_old_swap_requests(max_age_days=7)
        assert entry["id"] in expired

    def test_recent_swap_not_expired(self, write_db_path):
        """Recent swap requests are NOT expired."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-02-01",
            partner_id=2,
            partner_date="2025-02-02",
        )
        expired = db.expire_old_swap_requests(max_age_days=7)
        assert entry["id"] not in expired

    def test_already_resolved_swap_not_expired(self, write_db_path):
        """Approved/rejected swaps are not affected by expiry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        entry = db.create_swap_request(
            requester_id=1,
            requester_date="2025-03-01",
            partner_id=2,
            partner_date="2025-03-02",
        )
        db.resolve_swap_request(entry["id"], "approve")
        entries = db._load_swap_requests()
        for e in entries:
            if e["id"] == entry["id"]:
                e["created_at"] = (datetime.now() - timedelta(days=15)).isoformat(timespec="seconds")
        db._save_swap_requests(entries)

        expired = db.expire_old_swap_requests(max_age_days=7)
        assert entry["id"] not in expired


# ─── History endpoint tests ────────────────────────────────────────────────────


class TestHistoryEndpoint:
    def test_get_history_endpoint(self, app, _employee_ids):
        """GET /api/v1/shifts/swap/{id}/history returns history list."""
        emp_a_id, emp_b_id, emp_a_name, emp_b_name = _employee_ids
        planner, tok_p = _planner(app)
        try:
            # Create swap
            res = planner.post("/api/swap-requests", json={
                "requester_id": emp_a_id,
                "requester_date": "2025-05-01",
                "partner_id": emp_b_id,
                "partner_date": "2025-05-02",
            })
            assert res.status_code == 200, res.text
            swap_id = res.json()["id"]

            # Get history
            res = planner.get(f"/api/v1/shifts/swap/{swap_id}/history")
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["swap_id"] == swap_id
            assert isinstance(data["history"], list)
            assert len(data["history"]) >= 1
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)

    def test_get_history_not_found(self, app, _employee_ids):
        """GET history for non-existent swap returns 404."""
        planner, tok_p = _planner(app)
        try:
            res = planner.get("/api/v1/shifts/swap/99999/history")
            assert res.status_code == 404
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)

    def test_history_grows_after_reject(self, app, _employee_ids):
        """History has 2 entries after create + reject."""
        emp_a_id, emp_b_id, _a, _b = _employee_ids
        planner, tok_p = _planner(app)
        try:
            res = planner.post("/api/swap-requests", json={
                "requester_id": emp_a_id,
                "requester_date": "2025-06-10",
                "partner_id": emp_b_id,
                "partner_date": "2025-06-11",
            })
            swap_id = res.json()["id"]
            planner.patch(f"/api/swap-requests/{swap_id}/resolve", json={
                "action": "reject",
                "reject_reason": "Not possible",
            })
            res = planner.get(f"/api/v1/shifts/swap/{swap_id}/history")
            assert res.status_code == 200
            history = res.json()["history"]
            assert len(history) >= 2
            statuses = [h["status"] for h in history]
            assert "rejected" in statuses
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)


# ─── Expire endpoint tests ────────────────────────────────────────────────────


class TestExpireEndpoint:
    def test_expire_endpoint_requires_planer(self, app, _employee_ids):
        """Expire endpoint requires Planer role."""
        reader, tok_r = _make_client(app, "Leser", "reader_q072")
        try:
            res = reader.post("/api/v1/shifts/swap/expire")
            assert res.status_code in (401, 403)
        finally:
            from api.main import _sessions
            _sessions.pop(tok_r, None)

    def test_expire_endpoint_returns_count(self, app, _employee_ids):
        """Expire endpoint returns expired_count and expired_ids."""
        planner, tok_p = _planner(app)
        try:
            res = planner.post("/api/v1/shifts/swap/expire?max_age_days=7")
            assert res.status_code == 200
            data = res.json()
            assert "expired_count" in data
            assert "expired_ids" in data
            assert isinstance(data["expired_ids"], list)
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)


# ─── Email notification tests ────────────────────────────────────────────────


class TestEmailNotifications:
    def test_no_smtp_logs_not_fails(self, app, _employee_ids):
        """If SMTP is not configured, email sending is skipped gracefully (no exception)."""
        emp_a_id, emp_b_id, _a, _b = _employee_ids
        planner, tok_p = _planner(app)
        try:
            with patch("api.routers.misc._send_swap_email") as mock_email:
                res = planner.post("/api/swap-requests", json={
                    "requester_id": emp_a_id,
                    "requester_date": "2025-07-10",
                    "partner_id": emp_b_id,
                    "partner_date": "2025-07-11",
                })
                assert res.status_code == 200
                # _send_swap_email should have been called (even if it's a no-op without SMTP)
                mock_email.assert_called()
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)

    def test_approve_notifies_both_employees(self, app, _employee_ids):
        """Approval triggers email notification for both employees."""
        emp_a_id, emp_b_id, _a, _b = _employee_ids
        planner, tok_p = _planner(app)
        try:
            res = planner.post("/api/swap-requests", json={
                "requester_id": emp_a_id,
                "requester_date": "2025-08-10",
                "partner_id": emp_b_id,
                "partner_date": "2025-08-11",
            })
            swap_id = res.json()["id"]

            with patch("api.routers.misc._send_swap_email") as mock_email:
                res = planner.patch(f"/api/swap-requests/{swap_id}/resolve", json={
                    "action": "approve",
                    "resolved_by": "planer_test",
                })
                assert res.status_code == 200
                assert mock_email.call_count == 2  # both employees notified
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)

    def test_reject_notifies_only_requester(self, app, _employee_ids):
        """Rejection triggers email notification only for requester."""
        emp_a_id, emp_b_id, _a, _b = _employee_ids
        planner, tok_p = _planner(app)
        try:
            res = planner.post("/api/swap-requests", json={
                "requester_id": emp_a_id,
                "requester_date": "2025-09-10",
                "partner_id": emp_b_id,
                "partner_date": "2025-09-11",
            })
            swap_id = res.json()["id"]

            with patch("api.routers.misc._send_swap_email") as mock_email:
                res = planner.patch(f"/api/swap-requests/{swap_id}/resolve", json={
                    "action": "reject",
                    "reject_reason": "Urlaub nicht genehmigt",
                })
                assert res.status_code == 200
                assert mock_email.call_count == 1  # only requester
                call_kwargs = mock_email.call_args[1]
                assert call_kwargs["recipient_employee_id"] == emp_a_id
        finally:
            from api.main import _sessions
            _sessions.pop(tok_p, None)

    def test_send_swap_email_with_no_smtp_does_not_raise(self):
        """_send_swap_email falls back gracefully when SMTP not configured."""
        # Patch environment to ensure no SMTP host
        with patch.dict("os.environ", {"SP5_SMTP_HOST": "", "SP5_SMTP_ENABLED": "false"}):
            from api.routers.misc import _send_swap_email
            # Should not raise
            _send_swap_email(
                notification_type="swap_request",
                title="Test",
                message="Test message",
                recipient_employee_id=999,
            )
