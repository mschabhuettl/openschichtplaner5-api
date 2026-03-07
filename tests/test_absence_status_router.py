"""Tests for PATCH /api/absences/{id}/status — new endpoint coverage since v0.9.5.

Covers:
- approve flow → 200, ok=True
- reject flow with reason → 200, reason persisted
- reject flow without reason → 200, empty reject_reason
- status transition: approved → pending (clears reject_reason)
- invalid status → 422 (pydantic pattern validation)
- non-planer access → 403
- non-existent absence_id → 200 (endpoint creates status entry regardless)
"""

import pytest
from starlette.testclient import TestClient

# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_any_absence_id(client):
    """Return first absence ID from the DB, or None."""
    resp = client.get("/api/absences")
    if resp.status_code != 200:
        return None
    data = resp.json()
    if not data:
        return None
    first = data[0]
    return first.get("id") or first.get("ID")


# ── PATCH /api/absences/{id}/status ───────────────────────────────────────────


class TestAbsenceStatusApprove:
    def test_approve_returns_ok(self, write_client):
        """Approving an absence returns ok=True and status=approved."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "approved"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "approved"
        assert body["id"] == absence_id

    def test_approve_clears_reject_reason(self, write_client):
        """Approving an absence sets reject_reason to empty string."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        # First reject with a reason
        write_client.patch(
            f"/api/absences/{absence_id}/status",
            json={"status": "rejected", "reject_reason": "Urlaubssperre"},
        )
        # Then approve → reject_reason should be cleared
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "approved"}
        )
        assert resp.status_code == 200
        assert resp.json()["reject_reason"] == ""


class TestAbsenceStatusReject:
    def test_reject_with_reason(self, write_client):
        """Rejecting with a reason stores it in the response."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status",
            json={"status": "rejected", "reject_reason": "Urlaubssperre aktiv"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "rejected"
        assert "Urlaubssperre" in body["reject_reason"]

    def test_reject_without_reason(self, write_client):
        """Rejecting without a reason returns empty reject_reason."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "rejected"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["reject_reason"] == ""

    def test_rejected_removed_field_present(self, write_client):
        """Response always includes rejected_removed boolean."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "rejected"}
        )
        assert resp.status_code == 200
        assert "rejected_removed" in resp.json()


class TestAbsenceStatusTransitions:
    def test_pending_resets_status(self, write_client):
        """Setting status back to pending works."""
        absence_id = _get_any_absence_id(write_client)
        if absence_id is None:
            pytest.skip("No absences in test DB")
        # Approve first
        write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "approved"}
        )
        # Then reset to pending
        resp = write_client.patch(
            f"/api/absences/{absence_id}/status", json={"status": "pending"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_nonexistent_id_creates_entry(self, write_client):
        """A non-existent absence_id still returns 200 (creates status entry)."""
        resp = write_client.patch(
            "/api/absences/999999/status", json={"status": "approved"}
        )
        # endpoint always succeeds — it persists status even for unknown IDs
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestAbsenceStatusValidation:
    def test_invalid_status_rejected_by_pydantic(self, write_client):
        """Invalid status value returns 422 (Pydantic pattern validation)."""
        resp = write_client.patch(
            "/api/absences/1/status", json={"status": "invalid_status"}
        )
        assert resp.status_code == 422

    def test_missing_status_field(self, write_client):
        """Missing required status field returns 422."""
        resp = write_client.patch("/api/absences/1/status", json={})
        assert resp.status_code == 422

    def test_reject_reason_too_long(self, write_client):
        """reject_reason exceeding 500 chars returns 422."""
        resp = write_client.patch(
            "/api/absences/1/status",
            json={"status": "rejected", "reject_reason": "x" * 501},
        )
        assert resp.status_code == 422


class TestAbsenceStatusAuthorization:
    def test_unauthenticated_returns_401(self, app):
        """Unauthenticated request returns 401."""

        c = TestClient(app, raise_server_exceptions=False)
        resp = c.patch("/api/absences/1/status", json={"status": "approved"})
        assert resp.status_code == 401

    def test_leser_forbidden(self, leser_client):
        """Leser role cannot update absence status (requires Planer)."""
        resp = leser_client.patch("/api/absences/1/status", json={"status": "approved"})
        assert resp.status_code == 403
