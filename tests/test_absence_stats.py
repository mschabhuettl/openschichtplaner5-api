"""Tests for Q074: Absence Statistics endpoints."""



# ── Helpers ────────────────────────────────────────────────────────────────────

def _first_employee_id(sync_client) -> int:
    """Return the first available employee ID from the DB."""
    r = sync_client.get("/api/v1/employees")
    assert r.status_code == 200
    data = r.json()
    emps = data if isinstance(data, list) else data.get("items", [])
    assert emps, "No employees in test DB"
    return emps[0]["ID"]


def _first_group_id(sync_client) -> int:
    """Return the first available group ID from the DB."""
    r = sync_client.get("/api/v1/groups")
    assert r.status_code == 200
    data = r.json()
    groups = data if isinstance(data, list) else data.get("items", [])
    assert groups, "No groups in test DB"
    return groups[0]["ID"]


# ── Employee stats ─────────────────────────────────────────────────────────────


class TestEmployeeStats:
    def test_returns_200_for_valid_employee(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        assert r.status_code == 200

    def test_response_has_required_fields(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        data = r.json()
        for field in ("vacation_days", "sick_days", "other_days", "total_days", "by_month", "pending_requests"):
            assert field in data, f"Missing field: {field}"

    def test_total_equals_sum_of_parts(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        data = r.json()
        assert data["total_days"] == data["vacation_days"] + data["sick_days"] + data["other_days"]

    def test_by_month_has_12_entries(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        data = r.json()
        assert len(data["by_month"]) == 12

    def test_by_month_months_are_1_to_12(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        months = [e["month"] for e in r.json()["by_month"]]
        assert sorted(months) == list(range(1, 13))

    def test_by_month_entries_have_categories(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        for entry in r.json()["by_month"]:
            for key in ("vacation", "sick", "other"):
                assert key in entry, f"Missing key '{key}' in by_month entry"

    def test_employee_name_in_response(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        assert "employee_name" in r.json()

    def test_404_for_nonexistent_employee(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/employee/999999?year=2024")
        assert r.status_code == 404

    def test_requires_year_param(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}")
        assert r.status_code == 422  # Missing required query param

    def test_values_are_non_negative(self, sync_client):
        emp_id = _first_employee_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/employee/{emp_id}?year=2024")
        data = r.json()
        assert data["vacation_days"] >= 0
        assert data["sick_days"] >= 0
        assert data["other_days"] >= 0
        assert data["pending_requests"] >= 0

    def test_unauthenticated_returns_401(self, app):
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/absences/stats/employee/1?year=2024")
        assert r.status_code == 401


# ── Group stats ────────────────────────────────────────────────────────────────


class TestGroupStats:
    def test_returns_200_for_valid_group(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        assert r.status_code == 200

    def test_response_has_required_fields(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        data = r.json()
        for field in ("group_id", "group_name", "year", "employees", "group_totals", "top3_by_sick_days", "top3_by_vacation_days"):
            assert field in data, f"Missing field: {field}"

    def test_group_totals_has_all_keys(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        totals = r.json()["group_totals"]
        for key in ("vacation_days", "sick_days", "other_days", "total_days"):
            assert key in totals, f"Missing key in group_totals: {key}"

    def test_top3_lists_at_most_3(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        data = r.json()
        assert len(data["top3_by_sick_days"]) <= 3
        assert len(data["top3_by_vacation_days"]) <= 3

    def test_employees_list_is_list(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        assert isinstance(r.json()["employees"], list)

    def test_group_totals_consistent(self, sync_client):
        gid = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/absences/stats/group/{gid}?year=2024")
        totals = r.json()["group_totals"]
        assert totals["total_days"] == totals["vacation_days"] + totals["sick_days"] + totals["other_days"]

    def test_404_for_nonexistent_group(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/group/999999?year=2024")
        assert r.status_code == 404


# ── Overview stats ─────────────────────────────────────────────────────────────


class TestOverviewStats:
    def test_returns_200(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        assert r.status_code == 200

    def test_response_has_required_fields(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        data = r.json()
        for field in ("year", "company_totals", "groups", "by_month"):
            assert field in data, f"Missing field: {field}"

    def test_company_totals_has_all_keys(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        totals = r.json()["company_totals"]
        for key in ("vacation_days", "sick_days", "other_days", "total_days"):
            assert key in totals

    def test_by_month_has_12_entries(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        assert len(r.json()["by_month"]) == 12

    def test_groups_is_list(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        assert isinstance(r.json()["groups"], list)

    def test_year_in_response(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        assert r.json()["year"] == 2024

    def test_company_totals_consistent(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview?year=2024")
        totals = r.json()["company_totals"]
        assert totals["total_days"] == totals["vacation_days"] + totals["sick_days"] + totals["other_days"]

    def test_requires_year_param(self, sync_client):
        r = sync_client.get("/api/v1/absences/stats/overview")
        assert r.status_code == 422

    def test_unauthenticated_returns_401(self, app):
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/absences/stats/overview?year=2024")
        assert r.status_code == 401
