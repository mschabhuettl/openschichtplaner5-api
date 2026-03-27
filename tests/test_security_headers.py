"""Tests for security headers, CSP, and CSRF protection (Q024 + Q054)."""

import pytest
from api.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _get_csp(headers) -> str:
    """Extract CSP value from either enforcing or report-only header."""
    return headers.get(
        "content-security-policy",
        headers.get("content-security-policy-report-only", ""),
    )


@pytest.mark.anyio
async def test_security_headers_present(client: AsyncClient):
    """All security headers must be set on every response."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    h = resp.headers

    # Core security headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-xss-protection"] == "1; mode=block"

    # Content Security Policy (may be enforcing or report-only)
    csp = _get_csp(h)
    assert csp, "CSP header must be present"
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "img-src 'self' data: blob:" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    # Ensure dangerous directives are NOT present
    assert "'unsafe-eval'" not in csp

    # Permissions Policy
    pp = h["permissions-policy"]
    assert "camera=()" in pp
    assert "microphone=()" in pp
    assert "geolocation=()" in pp
    assert "payment=()" in pp

    # Cross-Origin isolation
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"


@pytest.mark.anyio
async def test_csp_report_only_mode(client: AsyncClient, monkeypatch):
    """When CSP_REPORT_ONLY=true, use Content-Security-Policy-Report-Only."""
    import api.main as main_module

    monkeypatch.setattr(main_module, "_CSP_REPORT_ONLY", True)
    resp = await client.get("/api/health")
    h = resp.headers
    assert "content-security-policy-report-only" in h
    csp = h["content-security-policy-report-only"]
    assert "default-src 'self'" in csp


@pytest.mark.anyio
async def test_security_headers_on_error_responses(client: AsyncClient):
    """Security headers must also be present on 404/error responses."""
    resp = await client.get("/api/nonexistent-endpoint-xyz")
    h = resp.headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    csp = _get_csp(h)
    assert csp, "CSP must be present on error responses"


@pytest.mark.anyio
async def test_security_headers_on_post(client: AsyncClient):
    """Security headers on POST responses (even failed auth)."""
    resp = await client.post(
        "/api/auth/login",
        json={"benutzername": "nobody", "passwort": "wrong"},
    )
    h = resp.headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"


@pytest.mark.anyio
async def test_cookie_samesite_strict(client: AsyncClient, tmp_path, monkeypatch):
    """Login cookie must use SameSite=Strict and HttpOnly."""
    resp = await client.post(
        "/api/auth/login",
        json={"benutzername": "admin", "passwort": "wrong"},
    )
    h = resp.headers
    assert h.get("x-content-type-options") == "nosniff"


@pytest.mark.anyio
async def test_cors_not_wildcard(client: AsyncClient):
    """CORS must not use wildcard '*' — only explicit origins."""
    resp = await client.options(
        "/api/health",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "*", "CORS must not use wildcard"
    assert "evil.example.com" not in acao


@pytest.mark.anyio
async def test_cors_allows_configured_origin(client: AsyncClient):
    """Configured origins (localhost dev) should be allowed."""
    resp = await client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )
    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao == "http://localhost:5173"


@pytest.mark.anyio
async def test_no_server_header_leak(client: AsyncClient):
    """Server header should not leak implementation details."""
    resp = await client.get("/api/health")
    server = resp.headers.get("server", "").lower()
    assert "uvicorn" not in server or "python" not in server
