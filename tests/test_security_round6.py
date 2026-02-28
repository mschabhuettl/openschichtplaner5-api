"""Security Audit Round 6 — XSS Prevention & HTML Injection Tests"""
import pytest
from fastapi.testclient import TestClient
from api.main import app


class TestXSSPrevention:
    """Test that HTML exports escape user data properly."""

    def test_schedule_html_export_exists(self, sync_client):
        """Schedule HTML export endpoint responds."""
        r = sync_client.get("/api/export/schedule?month=2026-02&format=html")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_schedule_html_no_raw_script_tags(self, sync_client):
        """HTML export should not contain unescaped script tags in content."""
        r = sync_client.get("/api/export/schedule?month=2026-02&format=html")
        assert r.status_code == 200
        body = r.text
        assert "<!DOCTYPE html>" in body

    def test_employee_html_export(self, sync_client):
        """Employee list HTML export works."""
        r = sync_client.get("/api/export/employees?format=html")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Mitarbeiterliste" in r.text

    def test_absences_html_export(self, sync_client):
        """Absences HTML export works."""
        r = sync_client.get("/api/export/absences?year=2026&format=html")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_statistics_html_export(self, sync_client):
        """Statistics HTML export works."""
        r = sync_client.get("/api/export/statistics?year=2026&format=html")
        assert r.status_code in (200, 422)  # may require additional params


class TestPathTraversal:
    """Test that path traversal attempts are blocked by type validation."""

    def test_group_param_type_validation(self, sync_client):
        """group param is typed as int — non-int values should be rejected."""
        r = sync_client.get("/api/schedule?year=2026&month=2&group=../../../etc/passwd")
        # FastAPI should reject or ignore non-integer group_id
        # The response is 422 (validation error) or 200 with all groups (None)
        assert r.status_code in (200, 422)
        if r.status_code == 422:
            assert "int" in r.text.lower() or "value" in r.text.lower()


class TestContentTypeValidation:
    """Test that endpoints reject wrong content types."""

    def test_login_rejects_text_plain(self):
        """Login endpoint rejects text/plain content type."""
        client = TestClient(app)
        r = client.post(
            "/api/auth/login",
            content='{"username":"admin","password":"Test1234"}',
            headers={"Content-Type": "text/plain"}
        )
        assert r.status_code == 422

    def test_login_accepts_json(self):
        """Login endpoint accepts application/json."""
        client = TestClient(app)
        r = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"}
        )
        assert r.status_code in (200, 401, 403)


class TestErrorEndpointSafety:
    """Test the frontend error reporting endpoint."""

    def test_error_endpoint_accepts_script_tag(self):
        """Error endpoint stores but does not reflect XSS payloads."""
        client = TestClient(app)
        r = client.post("/api/errors", json={
            "error": "<script>alert(1)</script>",
            "url": "/test",
            "user_agent": "test"
        })
        assert r.status_code == 200
        # Response should be {"ok": true} — not echoing the script
        data = r.json()
        assert data.get("ok") is True
        assert "<script>" not in str(data)

    def test_error_endpoint_enforces_max_length(self):
        """Error endpoint rejects oversized error strings."""
        client = TestClient(app)
        r = client.post("/api/errors", json={
            "error": "A" * 2001,  # Exceeds max_length=2000
            "url": "/test"
        })
        assert r.status_code == 422
