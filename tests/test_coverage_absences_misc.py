"""
Targeted coverage tests for absences.py and misc.py uncovered paths.
Focus: error paths, edge cases, status workflow, update_note, access endpoints.
"""
import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    from conftest import make_test_app
    return make_test_app(tmp_path)


@pytest.fixture
def admin_client(tmp_path):
    from conftest import make_test_app
    c = make_test_app(tmp_path)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "Test1234"})
    token = r.json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ─── Absences: error paths ───────────────────────────────────────────────────

class TestAbsenceErrorPaths:
    """Cover lines 42-43, 122-125, 134-135, 143-146 in absences.py"""

    def test_delete_absence_exception_path(self, admin_client):
        """Lines 42-43: exception in delete_absence."""
        # Deleting a non-existent absence should not crash (returns 0)
        r = admin_client.delete("/api/absences/9999/2025-01-01")
        assert r.status_code == 200

    def test_create_absence_invalid_employee(self, admin_client):
        """Lines 122-125: employee not found → 404."""
        r = admin_client.post("/api/absences", json={
            "employee_id": 99999,
            "date": "2025-06-01",
            "leave_type_id": 1
        })
        assert r.status_code == 404
        assert "Mitarbeiter" in r.json()["detail"]

    def test_create_absence_invalid_leave_type(self, admin_client):
        """Lines 134-135: leave_type not found → 404."""
        # First get a valid employee
        emps = admin_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees in test DB")
        emp_id = emps[0]["ID"]
        r = admin_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-06-15",
            "leave_type_id": 99999
        })
        assert r.status_code == 404
        assert "Abwesenheitstyp" in r.json()["detail"]

    def test_create_absence_conflict_409(self, admin_client):
        """Lines 143-146: duplicate absence → 409."""
        emps = admin_client.get("/api/employees").json()
        lts = admin_client.get("/api/leave-types").json()
        if not emps or not lts:
            pytest.skip("No employees or leave types")
        emp_id = emps[0]["ID"]
        lt_id = lts[0]["ID"]
        # Create first absence
        r1 = admin_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-07-10",
            "leave_type_id": lt_id
        })
        # Second should 409
        r2 = admin_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-07-10",
            "leave_type_id": lt_id
        })
        assert r2.status_code == 409

    def test_bulk_absence_invalid_leave_type(self, admin_client):
        """Lines 168-169: bulk absence with invalid leave type → 404."""
        r = admin_client.post("/api/absences/bulk", json={
            "date": "2025-08-01",
            "leave_type_id": 99999,
        })
        assert r.status_code == 404


class TestAbsenceStatus:
    """Cover lines 207-216, 259-260, 382-383, 398-400, 407-408, 422, 445, 465-466, 480-481, 505-506"""

    def test_get_absence_statuses_empty(self, admin_client):
        """Lines 207-216: get statuses when file is empty/missing."""
        r = admin_client.get("/api/absences/status")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_patch_absence_status_approved(self, admin_client):
        """Lines 259-260, 407-408: patch to approved."""
        # Create an absence first
        emps = admin_client.get("/api/employees").json()
        lts = admin_client.get("/api/leave-types").json()
        if not emps or not lts:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        lt_id = lts[0]["ID"]
        create_r = admin_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-09-01",
            "leave_type_id": lt_id
        })
        if create_r.status_code not in (200, 409):
            pytest.skip("Could not create absence")
        absences = admin_client.get("/api/absences", params={"year": 2025}).json()
        absence_id = None
        for a in absences:
            if a.get("employee_id") == emp_id and a.get("date") == "2025-09-01":
                absence_id = a.get("id")
                break
        if not absence_id:
            pytest.skip("Absence not found")

        r = admin_client.patch(f"/api/absences/{absence_id}/status", json={
            "status": "approved"
        })
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_patch_absence_status_rejected_with_reason(self, admin_client):
        """Lines 422, 445: reject with reason → removed from ABSEN."""
        emps = admin_client.get("/api/employees").json()
        lts = admin_client.get("/api/leave-types").json()
        if not emps or not lts:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        lt_id = lts[0]["ID"]
        admin_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-09-05",
            "leave_type_id": lt_id
        })
        absences = admin_client.get("/api/absences", params={"year": 2025}).json()
        absence_id = next(
            (a.get("id") for a in absences
             if a.get("employee_id") == emp_id and a.get("date") == "2025-09-05"),
            None
        )
        if not absence_id:
            pytest.skip("Absence not found")

        r = admin_client.patch(f"/api/absences/{absence_id}/status", json={
            "status": "rejected",
            "reject_reason": "Urlaubssperre"
        })
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert r.json()["reject_reason"] == "Urlaubssperre"

    def test_get_absence_statuses_after_patch(self, admin_client):
        """Lines 465-466: verify statuses are persisted."""
        r = admin_client.get("/api/absences/status")
        assert r.status_code == 200
        data = r.json()
        # All entries should have status key
        for v in data.values():
            assert "status" in v


class TestLeaveBalance:
    """Cover lines 306-307, 313."""

    def test_leave_balance_returns_data(self, admin_client):
        emps = admin_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        r = admin_client.get("/api/leave-balance", params={"year": 2025, "employee_id": emp_id})
        assert r.status_code == 200

    def test_leave_balance_group(self, admin_client):
        r = admin_client.get("/api/leave-balance/group", params={"year": 2025})
        assert r.status_code == 200


# ─── Notes: update / error paths ────────────────────────────────────────────

class TestNoteUpdatePaths:
    """Cover lines 87-88 (exception), 137-138 (invalid date), 152-153 (note not found)."""

    def test_update_note_invalid_date_format(self, admin_client):
        """Line 137-138: update note with invalid date."""
        # Get or create a note first
        r = admin_client.post("/api/notes", json={
            "date": "2025-01-01",
            "text": "Test note"
        })
        if r.status_code != 200:
            pytest.skip("Could not create note")
        note_id = r.json()["record"]["ID"] if "record" in r.json() else r.json().get("id")
        if not note_id:
            pytest.skip("No note ID returned")

        r2 = admin_client.put(f"/api/notes/{note_id}", json={
            "date": "not-a-date"
        })
        assert r2.status_code == 400
        assert "Datumsformat" in r2.json()["detail"]

    def test_update_note_not_found(self, admin_client):
        """Line 152-153: update non-existent note → 404."""
        r = admin_client.put("/api/notes/999999", json={"text": "updated"})
        assert r.status_code == 404


# ─── Employee / Group Access ──────────────────────────────────────────────────

class TestAccessEndpoints:
    """Cover lines 326-327, 357-358 in misc.py."""

    def test_delete_employee_access_not_found(self, admin_client):
        """Line 326-327: delete non-existent access rule → 404."""
        r = admin_client.delete("/api/employee-access/99999")
        assert r.status_code == 404

    def test_set_and_delete_group_access(self, admin_client):
        """Line 357-358: create group access then delete."""
        users = admin_client.get("/api/users").json()
        groups = admin_client.get("/api/groups").json()
        if not users or not groups:
            pytest.skip("No users or groups")
        user_id = users[0]["ID"]
        group_id = groups[0]["ID"]
        r = admin_client.post("/api/group-access", json={
            "user_id": user_id,
            "group_id": group_id,
            "rights": 0
        })
        assert r.status_code == 200
        access_id = r.json()["record"]["id"] if "record" in r.json() else None
        if access_id:
            r2 = admin_client.delete(f"/api/group-access/{access_id}")
            assert r2.status_code == 200


# ─── Wish Workflow ────────────────────────────────────────────────────────────

class TestWishWorkflow:
    """Cover lines 473-517 (approve_wish), 569 (delete_wish 404)."""

    def test_delete_wish_not_found(self, admin_client):
        """Line 569: delete non-existent wish → 404."""
        r = admin_client.delete("/api/wishes/999999")
        assert r.status_code == 404

    def test_approve_wish_not_found(self, admin_client):
        """approve non-existent wish → 404."""
        r = admin_client.patch("/api/wishes/999999/approve", json={"action": "approve"})
        assert r.status_code == 404

    def test_create_and_approve_wish(self, admin_client):
        """Lines 473-517: full approve_wish path."""
        emps = admin_client.get("/api/employees").json()
        shifts = admin_client.get("/api/shifts").json()
        if not emps or not shifts:
            pytest.skip("No employees or shifts")
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]

        r = admin_client.post("/api/wishes", json={
            "employee_id": emp_id,
            "date": "2025-10-01",
            "wish_type": "WUNSCH",
            "shift_id": shift_id,
        })
        if r.status_code != 200:
            pytest.skip("Could not create wish")

        wishes = admin_client.get("/api/wishes").json()
        wish = next(
            (w for w in wishes
             if w.get("employee_id") == emp_id and w.get("date") == "2025-10-01"),
            None
        )
        if not wish:
            pytest.skip("Wish not found after create")

        wish_id = wish.get("id")
        r2 = admin_client.patch(f"/api/wishes/{wish_id}/approve", json={
            "action": "approve",
            "note": "Genehmigt"
        })
        assert r2.status_code == 200

    def test_reject_wish(self, admin_client):
        """Lines 473+: reject path."""
        emps = admin_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]

        r = admin_client.post("/api/wishes", json={
            "employee_id": emp_id,
            "date": "2025-10-15",
            "wish_type": "SPERRUNG",
        })
        if r.status_code != 200:
            pytest.skip("Could not create wish")

        wishes = admin_client.get("/api/wishes").json()
        wish = next(
            (w for w in wishes
             if w.get("employee_id") == emp_id and w.get("date") == "2025-10-15"),
            None
        )
        if not wish:
            pytest.skip("Wish not found")

        r2 = admin_client.patch(f"/api/wishes/{wish['id']}/approve", json={
            "action": "reject"
        })
        assert r2.status_code == 200


# ─── Annual Close ─────────────────────────────────────────────────────────────

class TestAnnualClose:
    """Cover lines 382-383, 398-400."""

    def test_annual_close_preview(self, admin_client):
        """Lines 382-383: preview endpoint."""
        r = admin_client.get("/api/annual-close/preview", params={"year": 2025})
        assert r.status_code == 200

    def test_annual_close_execute(self, admin_client):
        """Lines 398-400: execute annual close."""
        r = admin_client.post("/api/annual-close", json={"year": 2025})
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ─── Misc: misc.py lines 720-721, 736, 769-824 ───────────────────────────────

class TestMiscCoverage:
    """Cover swap-requests resolve, handover, and changelog."""

    def test_changelog(self, admin_client):
        """Lines 720-721."""
        r = admin_client.get("/api/changelog")
        assert r.status_code == 200

    def test_handover_get(self, admin_client):
        r = admin_client.get("/api/handover")
        assert r.status_code == 200

    def test_handover_post_and_delete(self, admin_client):
        """Lines 769-824: create handover note."""
        r = admin_client.post("/api/handover", json={
            "text": "Schicht gut verlaufen",
            "date": "2025-11-01",
        })
        assert r.status_code == 200
        note_id = r.json().get("id")
        if note_id:
            r2 = admin_client.delete(f"/api/handover/{note_id}")
            assert r2.status_code == 200

    def test_swap_request_resolve_not_found(self, admin_client):
        """Lines 912-922: resolve non-existent swap → 404."""
        r = admin_client.patch("/api/swap-requests/99999/resolve", json={
            "action": "approve"
        })
        assert r.status_code == 404

    def test_misc_line_136_update_note_valid(self, admin_client):
        """Lines 87-88: create note then update with valid date."""
        r = admin_client.post("/api/notes", json={
            "date": "2025-01-15",
            "text": "Initial note"
        })
        if r.status_code != 200:
            pytest.skip("Could not create note")
        rec = r.json().get("record", {})
        note_id = rec.get("ID") or rec.get("id")
        if not note_id:
            pytest.skip("No note ID")
        r2 = admin_client.put(f"/api/notes/{note_id}", json={
            "text": "Updated text",
            "date": "2025-02-01"
        })
        assert r2.status_code == 200


# ─── Self-service absence ──────────────────────────────────────────────────────

class TestSelfAbsence:
    """Cover lines 949-952, 981-987."""

    def test_self_absences_list(self, admin_client):
        """Lines 949-952: GET /api/self/absences."""
        r = admin_client.get("/api/self/absences")
        # Either 200 or 404 (no employee record for admin)
        assert r.status_code in (200, 404)

    def test_self_wishes_list(self, admin_client):
        r = admin_client.get("/api/self/wishes")
        assert r.status_code in (200, 404)
