"""Tests for POST /api/schedule/bulk-group (Bulk Group Assignment)."""

from starlette.testclient import TestClient


class TestBulkGroupAssign:
    """Tests for the bulk group assignment endpoint."""

    def _setup_group_with_members(self, client: TestClient):
        """Create a group, employees, and add employees to the group."""
        # Create a group
        grp = client.post("/api/groups", json={"NAME": "TestGruppe", "SHORTNAME": "TG"})
        assert grp.status_code == 200, grp.text
        group_id = grp.json()["record"]["ID"]

        # Create employees
        emp_ids = []
        for i in range(3):
            r = client.post("/api/employees", json={
                "NAME": f"BulkTest{i}",
                "SHORTNAME": f"BT{i}",
            })
            assert r.status_code == 200, r.text
            emp_ids.append(r.json()["record"]["ID"])

        # Add employees to group
        for eid in emp_ids:
            r = client.post(f"/api/groups/{group_id}/members", json={"employee_id": eid})
            assert r.status_code == 200, r.text

        # Get a shift
        shifts = client.get("/api/shifts").json()
        assert len(shifts) > 0, "Need at least one shift for testing"
        shift_id = shifts[0]["ID"]

        return group_id, emp_ids, shift_id

    def test_bulk_group_assign_success(self, admin_client: TestClient):
        """Assign a shift to all group members for a date range."""
        group_id, emp_ids, shift_id = self._setup_group_with_members(admin_client)

        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-06-01",
            "date_to": "2026-06-03",
            "overwrite": True,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["employees"] == 3
        assert data["days"] == 3
        assert data["total_assignments"] == 9  # 3 employees × 3 days
        assert data["created"] == 9

    def test_bulk_group_assign_overwrite(self, admin_client: TestClient):
        """Second assignment with overwrite=True should update."""
        group_id, emp_ids, shift_id = self._setup_group_with_members(admin_client)

        # First assign
        admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-07-01",
            "date_to": "2026-07-02",
            "overwrite": True,
        })

        # Second assign (overwrite)
        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-07-01",
            "date_to": "2026-07-02",
            "overwrite": True,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["updated"] == 6  # all 6 entries updated

    def test_bulk_group_assign_no_overwrite(self, admin_client: TestClient):
        """With overwrite=False, existing entries should be skipped."""
        group_id, emp_ids, shift_id = self._setup_group_with_members(admin_client)

        # First assign
        admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-08-01",
            "date_to": "2026-08-01",
            "overwrite": True,
        })

        # Second assign without overwrite - should not create duplicates but may add
        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-08-01",
            "date_to": "2026-08-01",
            "overwrite": False,
        })
        assert r.status_code == 200

    def test_bulk_group_assign_missing_group_and_employees(self, admin_client: TestClient):
        """Should fail if neither group_id nor employee_ids provided."""
        shifts = admin_client.get("/api/shifts").json()
        shift_id = shifts[0]["ID"]

        r = admin_client.post("/api/schedule/bulk-group", json={
            "shift_id": shift_id,
            "date_from": "2026-06-01",
            "date_to": "2026-06-03",
        })
        assert r.status_code == 400
        assert "group_id" in r.json()["detail"].lower() or "employee_ids" in r.json()["detail"].lower()

    def test_bulk_group_assign_invalid_shift(self, admin_client: TestClient):
        """Should fail with non-existent shift."""
        group_id, _, _ = self._setup_group_with_members(admin_client)

        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": 99999,
            "date_from": "2026-06-01",
            "date_to": "2026-06-01",
        })
        assert r.status_code == 404

    def test_bulk_group_assign_invalid_date_range(self, admin_client: TestClient):
        """Should fail if date_from > date_to."""
        group_id, _, shift_id = self._setup_group_with_members(admin_client)

        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-06-05",
            "date_to": "2026-06-01",
        })
        assert r.status_code == 400
        assert "date_from" in r.json()["detail"].lower()

    def test_bulk_group_assign_with_employee_ids(self, admin_client: TestClient):
        """Should work with explicit employee_ids instead of group_id."""
        _, emp_ids, shift_id = self._setup_group_with_members(admin_client)

        r = admin_client.post("/api/schedule/bulk-group", json={
            "employee_ids": emp_ids[:2],
            "shift_id": shift_id,
            "date_from": "2026-09-01",
            "date_to": "2026-09-01",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["employees"] == 2
        assert data["days"] == 1
        assert data["total_assignments"] == 2

    def test_bulk_group_assign_requires_planer(self, leser_client: TestClient):
        """Should deny access for Leser role."""
        r = leser_client.post("/api/schedule/bulk-group", json={
            "group_id": 1,
            "shift_id": 1,
            "date_from": "2026-06-01",
            "date_to": "2026-06-01",
        })
        assert r.status_code in (401, 403)

    def test_bulk_group_assign_single_day(self, admin_client: TestClient):
        """Should work for a single day (date_from == date_to)."""
        group_id, emp_ids, shift_id = self._setup_group_with_members(admin_client)

        r = admin_client.post("/api/schedule/bulk-group", json={
            "group_id": group_id,
            "shift_id": shift_id,
            "date_from": "2026-10-15",
            "date_to": "2026-10-15",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == 1
        assert data["total_assignments"] == 3
