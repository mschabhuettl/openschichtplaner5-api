"""Tests for Q059: Soft-delete for employees (deactivate instead of delete)."""


class TestSoftDeleteEmployee:
    """DELETE /api/employees/{id} should set HIDE=true, not hard-delete."""

    def test_soft_delete_preserves_record(self, admin_client):
        """Soft-deleting an employee should keep the record in the database."""
        res = admin_client.post("/api/employees", json={"NAME": "SoftDel", "FIRSTNAME": "Test", "SHORTNAME": "SDT"})
        assert res.status_code == 200
        emp_id = res.json()["record"]["ID"]

        # Delete (deactivate)
        res = admin_client.delete(f"/api/employees/{emp_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True

        # Employee should NOT appear in default listing (active only)
        res = admin_client.get("/api/employees")
        assert res.status_code == 200
        ids = [e["ID"] for e in res.json()]
        assert emp_id not in ids

        # Employee SHOULD appear when include_hidden=true
        res = admin_client.get("/api/employees?include_hidden=true")
        assert res.status_code == 200
        all_ids = [e["ID"] for e in res.json()]
        assert emp_id in all_ids

        # Employee should be marked as HIDE=True
        hidden_emp = next(e for e in res.json() if e["ID"] == emp_id)
        assert hidden_emp["HIDE"] is True or hidden_emp["HIDE"] == 1

    def test_soft_delete_preserves_historical_data(self, admin_client):
        """Soft-deleting should NOT delete schedule/absence entries — record remains accessible."""
        res = admin_client.post("/api/employees", json={"NAME": "HistKeep", "FIRSTNAME": "Data", "SHORTNAME": "HKD"})
        assert res.status_code == 200
        emp_id = res.json()["record"]["ID"]

        # Deactivate the employee
        res = admin_client.delete(f"/api/employees/{emp_id}")
        assert res.status_code == 200

        # The record should still exist and be retrievable by ID
        res = admin_client.get(f"/api/employees/{emp_id}")
        assert res.status_code == 200
        assert res.json()["ID"] == emp_id


class TestEmployeeFiltering:
    """GET /api/employees with include_hidden parameter."""

    def test_default_returns_only_active(self, admin_client):
        res = admin_client.post("/api/employees", json={"NAME": "FilterTest", "FIRSTNAME": "A", "SHORTNAME": "FTA"})
        emp_id = res.json()["record"]["ID"]
        admin_client.delete(f"/api/employees/{emp_id}")

        res = admin_client.get("/api/employees")
        ids = [e["ID"] for e in res.json()]
        assert emp_id not in ids

    def test_include_hidden_returns_all(self, admin_client):
        res = admin_client.post("/api/employees", json={"NAME": "FilterActive", "FIRSTNAME": "B", "SHORTNAME": "FAB"})
        active_id = res.json()["record"]["ID"]

        res = admin_client.post("/api/employees", json={"NAME": "FilterInactive", "FIRSTNAME": "C", "SHORTNAME": "FIC"})
        inactive_id = res.json()["record"]["ID"]
        admin_client.delete(f"/api/employees/{inactive_id}")

        res = admin_client.get("/api/employees?include_hidden=true")
        ids = [e["ID"] for e in res.json()]
        assert active_id in ids
        assert inactive_id in ids


class TestReactivateEmployee:
    """PUT /api/employees/{id}/activate should set HIDE=false."""

    def test_reactivate_employee(self, admin_client):
        res = admin_client.post("/api/employees", json={"NAME": "Reactivate", "FIRSTNAME": "Me", "SHORTNAME": "RME"})
        emp_id = res.json()["record"]["ID"]
        admin_client.delete(f"/api/employees/{emp_id}")

        # Verify hidden
        res = admin_client.get("/api/employees")
        assert emp_id not in [e["ID"] for e in res.json()]

        # Reactivate
        res = admin_client.put(f"/api/employees/{emp_id}/activate")
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert res.json()["activated"] >= 1

        # Should be visible again
        res = admin_client.get("/api/employees")
        assert emp_id in [e["ID"] for e in res.json()]

    def test_reactivate_nonexistent_returns_404(self, admin_client):
        res = admin_client.put("/api/employees/999999/activate")
        assert res.status_code == 404

    def test_reactivate_already_active_is_idempotent(self, admin_client):
        res = admin_client.post("/api/employees", json={"NAME": "AlreadyActive", "FIRSTNAME": "X", "SHORTNAME": "AAX"})
        emp_id = res.json()["record"]["ID"]

        res = admin_client.put(f"/api/employees/{emp_id}/activate")
        assert res.status_code == 200
        assert res.json()["ok"] is True


class TestInactiveNotInScheduleDropdowns:
    """Inactive employees should not appear in active employee listings."""

    def test_inactive_not_in_default_listing(self, admin_client):
        res = admin_client.post("/api/employees", json={"NAME": "DropdownTest", "FIRSTNAME": "Z", "SHORTNAME": "DDZ"})
        emp_id = res.json()["record"]["ID"]
        admin_client.delete(f"/api/employees/{emp_id}")

        res = admin_client.get("/api/employees")
        ids = [e["ID"] for e in res.json()]
        assert emp_id not in ids, "Inactive employee should not appear in schedule assignment dropdowns"
