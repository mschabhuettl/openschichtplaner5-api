"""Tests for Q084: Employee Qualification Matrix endpoints."""

import secrets

# ── Helpers ────────────────────────────────────────────────────────────────────


def _inject_role(role: str) -> str:
    """Inject a session token with the given role, return token."""
    from api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 800 + abs(hash(role)) % 10,
        "NAME": f"test_{role.lower()}",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 255 if role == "Admin" else (2 if role == "Planer" else 1),
    }
    return tok


def _remove_token(tok: str) -> None:
    from api.main import _sessions

    _sessions.pop(tok, None)


def _first_group_id(sync_client) -> int:
    r = sync_client.get("/api/v1/groups")
    assert r.status_code == 200
    data = r.json()
    groups = data if isinstance(data, list) else data.get("items", [])
    assert groups, "No groups in test DB"
    return groups[0]["ID"]


# ── Qualification Matrix Tests ─────────────────────────────────────────────────


class TestQualificationMatrix:
    def test_returns_200(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        assert r.status_code == 200

    def test_response_has_required_keys(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        assert "qualifications" in data, "Missing 'qualifications' key"
        assert "employees" in data, "Missing 'employees' key"

    def test_qualifications_is_list(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        assert isinstance(data["qualifications"], list)

    def test_employees_is_list(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        assert isinstance(data["employees"], list)

    def test_employee_row_has_required_fields(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        if not data["employees"]:
            return  # no employees, skip
        row = data["employees"][0]
        for field in ("id", "name", "group_name", "qualifications"):
            assert field in row, f"Missing field '{field}' in employee row"

    def test_employee_qualifications_is_dict(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        if not data["employees"]:
            return
        row = data["employees"][0]
        assert isinstance(row["qualifications"], dict)

    def test_employee_qualifications_values_are_bool(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        all_quals = data["qualifications"]
        for row in data["employees"]:
            for q in all_quals:
                assert isinstance(row["qualifications"][q], bool), (
                    f"Qualification '{q}' value should be bool"
                )

    def test_employee_qual_keys_match_header(self, sync_client):
        """All employee qualification dict keys must match the header qualifications."""
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        data = r.json()
        header = set(data["qualifications"])
        for row in data["employees"]:
            assert set(row["qualifications"].keys()) == header, (
                "Employee qualification keys don't match header"
            )

    def test_qualifications_are_sorted(self, sync_client):
        r = sync_client.get("/api/v1/employees/qualification-matrix")
        quals = r.json()["qualifications"]
        assert quals == sorted(quals), "Qualifications should be sorted alphabetically"

    def test_filter_by_group_id(self, sync_client):
        group_id = _first_group_id(sync_client)
        r = sync_client.get(
            f"/api/v1/employees/qualification-matrix?group_id={group_id}"
        )
        assert r.status_code == 200
        data = r.json()
        assert "qualifications" in data
        assert "employees" in data

    def test_filter_by_nonexistent_group_returns_empty(self, sync_client):
        r = sync_client.get(
            "/api/v1/employees/qualification-matrix?group_id=999999"
        )
        assert r.status_code == 200
        data = r.json()
        assert data["employees"] == []
        assert data["qualifications"] == []

    def test_requires_auth(self, sync_client):
        """Endpoint should reject unauthenticated requests."""
        from api.main import app
        from starlette.testclient import TestClient

        bare = TestClient(app, raise_server_exceptions=False)
        r = bare.get("/api/v1/employees/qualification-matrix")
        assert r.status_code in (401, 403)

    def test_leser_role_is_forbidden(self, sync_client):
        from api.main import app
        from starlette.testclient import TestClient

        tok = _inject_role("Leser")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get(
                "/api/v1/employees/qualification-matrix",
                headers={"X-Auth-Token": tok},
            )
            assert r.status_code == 403
        finally:
            _remove_token(tok)

    def test_planer_role_allowed(self, sync_client):
        from api.main import app
        from starlette.testclient import TestClient

        tok = _inject_role("Planer")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get(
                "/api/v1/employees/qualification-matrix",
                headers={"X-Auth-Token": tok},
            )
            assert r.status_code == 200
        finally:
            _remove_token(tok)


# ── Qualification Stats Tests ──────────────────────────────────────────────────


class TestQualificationStats:
    def test_returns_200(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        assert r.status_code == 200

    def test_response_has_qualifications_key(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        assert "qualifications" in data

    def test_qualifications_is_list(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        assert isinstance(data["qualifications"], list)

    def test_qual_entry_has_required_fields(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        for entry in data["qualifications"]:
            for field in ("name", "count", "percentage", "employees"):
                assert field in entry, f"Missing field '{field}' in qual entry"

    def test_count_matches_employees_length(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        for entry in data["qualifications"]:
            assert entry["count"] == len(entry["employees"]), (
                f"count mismatch for {entry['name']}"
            )

    def test_percentage_between_0_and_100(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        for entry in data["qualifications"]:
            assert 0.0 <= entry["percentage"] <= 100.0, (
                f"Percentage out of range for {entry['name']}: {entry['percentage']}"
            )

    def test_employee_entries_have_id_and_name(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        data = r.json()
        for entry in data["qualifications"]:
            for emp in entry["employees"]:
                assert "id" in emp
                assert "name" in emp

    def test_filter_by_group_id(self, sync_client):
        group_id = _first_group_id(sync_client)
        r = sync_client.get(f"/api/v1/qualifications/stats?group_id={group_id}")
        assert r.status_code == 200
        data = r.json()
        assert "qualifications" in data

    def test_filter_by_nonexistent_group_returns_empty(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats?group_id=999999")
        assert r.status_code == 200
        data = r.json()
        assert data["qualifications"] == []

    def test_stats_sorted_alphabetically(self, sync_client):
        r = sync_client.get("/api/v1/qualifications/stats")
        names = [e["name"] for e in r.json()["qualifications"]]
        assert names == sorted(names), "Stats should be sorted alphabetically"

    def test_requires_auth(self, sync_client):
        from api.main import app
        from starlette.testclient import TestClient

        bare = TestClient(app, raise_server_exceptions=False)
        r = bare.get("/api/v1/qualifications/stats")
        assert r.status_code in (401, 403)

    def test_consistency_with_matrix(self, sync_client):
        """Stats counts should be consistent with matrix data."""
        matrix_r = sync_client.get("/api/v1/employees/qualification-matrix")
        stats_r = sync_client.get("/api/v1/qualifications/stats")
        matrix = matrix_r.json()
        stats = stats_r.json()

        # Build counts from matrix
        matrix_counts: dict[str, int] = {}
        for q in matrix["qualifications"]:
            matrix_counts[q] = sum(
                1 for row in matrix["employees"] if row["qualifications"].get(q)
            )

        # Compare to stats
        stats_counts = {e["name"]: e["count"] for e in stats["qualifications"]}
        assert matrix_counts == stats_counts, (
            "Matrix and stats counts disagree"
        )
