"""Tests for new features: Warnings Center, Dashboard Stats, Security/Auth checks,
Input validation, Schedule Conflicts API."""
import pytest
import secrets
from starlette.testclient import TestClient


# ─────────────────────────────────────────────────────────────
# Warnings Center API
# ─────────────────────────────────────────────────────────────

class TestWarningsAPI:
    """Tests for /api/warnings endpoint (Warnings Center)."""

    def test_warnings_returns_200(self, sync_client):
        resp = sync_client.get("/api/warnings")
        assert resp.status_code == 200

    def test_warnings_response_shape(self, sync_client):
        resp = sync_client.get("/api/warnings")
        data = resp.json()
        assert "warnings" in data
        assert "count" in data
        assert isinstance(data["warnings"], list)
        assert isinstance(data["count"], int)
        assert data["count"] == len(data["warnings"])

    def test_warnings_with_explicit_year_month(self, sync_client):
        resp = sync_client.get("/api/warnings?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert "warnings" in data

    def test_warnings_invalid_month(self, sync_client):
        resp = sync_client.get("/api/warnings?year=2024&month=13")
        assert resp.status_code == 400

    def test_warnings_invalid_month_zero(self, sync_client):
        resp = sync_client.get("/api/warnings?year=2024&month=0")
        assert resp.status_code == 400

    def test_warnings_each_has_required_fields(self, sync_client):
        """Each warning item must have type, severity, title."""
        resp = sync_client.get("/api/warnings?year=2024&month=6")
        data = resp.json()
        for w in data["warnings"]:
            assert "type" in w, f"Warning missing 'type': {w}"
            assert "severity" in w, f"Warning missing 'severity': {w}"
            assert "title" in w, f"Warning missing 'title': {w}"

    def test_warnings_requires_auth(self, app):
        """Unauthenticated request should return 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/warnings")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────
# Dashboard Stats with year/month parameters
# ─────────────────────────────────────────────────────────────

class TestDashboardStatsParams:
    """Dashboard stats with explicit year/month parameters."""

    def test_stats_with_year_and_month(self, sync_client):
        resp = sync_client.get("/api/dashboard/stats?year=2024&month=6")
        assert resp.status_code == 200

    def test_stats_with_year_only(self, sync_client):
        resp = sync_client.get("/api/dashboard/stats?year=2024")
        assert resp.status_code == 200

    def test_stats_invalid_month(self, sync_client):
        resp = sync_client.get("/api/dashboard/stats?year=2024&month=0")
        # API should reject invalid months
        assert resp.status_code in (400, 422)

    def test_stats_invalid_month_high(self, sync_client):
        resp = sync_client.get("/api/dashboard/stats?year=2024&month=13")
        assert resp.status_code in (400, 422)

    def test_dashboard_summary_with_month_params(self, sync_client):
        resp = sync_client.get("/api/dashboard/summary?year=2024&month=3")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────
# Schedule Conflicts API
# ─────────────────────────────────────────────────────────────

class TestScheduleConflictsAPI:
    """Tests for /api/schedule/conflicts endpoint."""

    def test_conflicts_returns_200(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=6")
        assert resp.status_code == 200

    def test_conflicts_response_shape(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=6")
        data = resp.json()
        assert "conflicts" in data
        assert isinstance(data["conflicts"], list)

    def test_conflicts_requires_auth(self, app):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/schedule/conflicts?year=2024&month=6")
        assert resp.status_code == 401

    def test_conflicts_invalid_month(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=0")
        assert resp.status_code in (400, 422)


# ─────────────────────────────────────────────────────────────
# Security: Admin-only Endpoints
# ─────────────────────────────────────────────────────────────

class TestAdminOnlyEndpoints:
    """Verify that admin-only endpoints reject non-admin users."""

    @pytest.fixture
    def planer_client(self, app):
        """TestClient authenticated as Planer (non-admin)."""
        from api.main import _sessions
        tok = secrets.token_hex(20)
        _sessions[tok] = {
            'ID': 800,
            'NAME': 'test_planer',
            'role': 'Planer',
            'ADMIN': False,
            'RIGHTS': 2,
        }
        client = TestClient(app, raise_server_exceptions=False)
        client.headers.update({'X-Auth-Token': tok})
        yield client
        _sessions.pop(tok, None)

    def test_employee_access_admin_only(self, planer_client):
        resp = planer_client.get("/api/employee-access")
        assert resp.status_code == 403

    def test_group_access_admin_only(self, planer_client):
        resp = planer_client.get("/api/group-access")
        assert resp.status_code == 403

    def test_user_management_admin_only(self, planer_client):
        """Creating users is admin-only."""
        resp = planer_client.post("/api/users", json={
            "USERNAME": "newuser",
            "PASSWORD": "pass123",
            "role": "Viewer",
        })
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────
# Security: Unauthenticated access rejected
# ─────────────────────────────────────────────────────────────

class TestUnauthenticatedAccess:
    """Core protected endpoints must return 401 without a token."""

    PROTECTED_ENDPOINTS = [
        "/api/employees",
        "/api/schedule?year=2024&month=6",
        "/api/dashboard/summary",
        "/api/warnings",
        "/api/schedule/conflicts?year=2024&month=6",
        "/api/users",
    ]

    @pytest.fixture
    def anon_client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
    def test_endpoint_requires_auth(self, anon_client, path):
        resp = anon_client.get(path)
        assert resp.status_code == 401, f"Expected 401 for {path}, got {resp.status_code}"


# ─────────────────────────────────────────────────────────────
# Input Validation: 422 on invalid payloads
# ─────────────────────────────────────────────────────────────

class TestInputValidation:
    """Ensure the API returns 422 (or 400) for malformed payloads."""

    def test_create_employee_empty_name(self, sync_client):
        resp = sync_client.post("/api/employees", json={"NAME": "", "KUERZEL": "XX"})
        assert resp.status_code in (400, 422)

    def test_create_shift_empty_name(self, sync_client):
        resp = sync_client.post("/api/shifts", json={"NAME": ""})
        assert resp.status_code in (400, 422)

    def test_create_group_empty_name(self, sync_client):
        resp = sync_client.post("/api/groups", json={"NAME": ""})
        assert resp.status_code in (400, 422)

    def test_login_missing_username(self, app):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/auth/login", json={"password": "x"})
        assert resp.status_code in (400, 422)

    def test_login_missing_password(self, app):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/auth/login", json={"username": "admin"})
        assert resp.status_code in (400, 422)

    def test_schedule_invalid_employee_id(self, sync_client):
        """Non-integer employee_id should be rejected."""
        resp = sync_client.post("/api/schedule", json={
            "employee_id": "abc",
            "date": "2024-06-01",
            "shift_id": 1
        })
        assert resp.status_code in (400, 422)
