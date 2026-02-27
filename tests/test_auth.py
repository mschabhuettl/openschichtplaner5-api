"""
Tests for authentication: login, invalid credentials, token validation.

Note: sync_client has X-Auth-Token pre-set (admin).
Tests that need NO auth use a raw fresh TestClient.
Tests that need login calls share a session-scoped token to avoid rate limits.
"""
import secrets
import pytest
from starlette.testclient import TestClient


# ── Session-scoped login (only login once to avoid rate limit) ────────────────

@pytest.fixture(scope='session')
def admin_login_result(sync_client: TestClient):
    """Log in once per session — reuses the already-logged-in sync_client session."""
    # sync_client already logged in via conftest; grab the token from its headers
    token = sync_client.headers.get('X-Auth-Token')
    assert token, "sync_client should have auth token from conftest"
    return {'ok': True, 'token': token}


# ── Login endpoint tests ──────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, sync_client: TestClient):
        """Fresh login returns ok=True with a token."""
        # Use a new client to bypass rate-limit tracking on shared client
        from starlette.testclient import TestClient as TC
        from api.main import app
        with TC(app, raise_server_exceptions=True) as c:
            res = c.post(
                '/api/auth/login',
                json={'username': 'admin', 'password': 'Test1234'},
            )
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True
        assert isinstance(data['token'], str) and len(data['token']) > 20
        assert 'user' in data
        user = data['user']
        assert 'role' in user
        assert user['role'] in ('Admin', 'Planer', 'Leser')

    def test_login_wrong_password(self, sync_client: TestClient):
        """Wrong password → 401 or 429 (rate limit); not 200."""
        res = sync_client.post(
            '/api/auth/login',
            json={'username': 'admin', 'password': 'definitely_wrong'},
        )
        assert res.status_code in (401, 429)

    def test_login_unknown_user(self, sync_client: TestClient):
        """Unknown username → 401 or 429."""
        res = sync_client.post(
            '/api/auth/login',
            json={'username': 'zzz_does_not_exist', 'password': 'whatever'},
        )
        assert res.status_code in (401, 429)

    def test_login_missing_fields(self, sync_client: TestClient):
        """Missing body fields → 422."""
        res = sync_client.post('/api/auth/login', json={})
        assert res.status_code == 422

    def test_login_error_has_detail(self, sync_client: TestClient):
        """Auth errors return {"detail": "..."}."""
        res = sync_client.post(
            '/api/auth/login',
            json={'username': 'nobody', 'password': 'bad'},
        )
        assert res.status_code in (401, 429)
        assert 'detail' in res.json()


# ── Token validation tests ────────────────────────────────────────────────────

class TestTokenValidation:
    def test_authenticated_request_works(self, sync_client: TestClient):
        """Authenticated client can access protected endpoint."""
        res = sync_client.get('/api/employees')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_no_token_rejected(self, app):
        """Request without token → 401 (uses fresh unauthenticated client)."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            res = raw.get('/api/employees')
        assert res.status_code == 401
        body = res.json()
        assert 'detail' in body
        assert isinstance(body['detail'], str)

    def test_invalid_token_rejected(self, app):
        """Garbage token → 401."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            res = raw.get(
                '/api/employees',
                headers={'X-Auth-Token': 'thisisnotavalidtoken12345'},
            )
        assert res.status_code == 401
        assert 'detail' in res.json()

    def test_logout_and_reuse(self, app):
        """After logout, token should no longer be valid."""
        from api.main import _sessions
        tok = secrets.token_hex(32)
        _sessions[tok] = {'ID': 1, 'NAME': 'admin', 'role': 'Admin', 'ADMIN': True, 'RIGHTS': 255}

        with TestClient(app, raise_server_exceptions=False) as c:
            r1 = c.get('/api/employees', headers={'X-Auth-Token': tok})
            assert r1.status_code == 200

            r_out = c.post('/api/auth/logout', headers={'X-Auth-Token': tok})
            assert r_out.status_code == 200
            assert r_out.json()['ok'] is True

            r2 = c.get('/api/employees', headers={'X-Auth-Token': tok})
            assert r2.status_code == 401

    def test_logout_without_token_is_ok(self, sync_client: TestClient):
        """Logout without sending a token → 200 (idempotent)."""
        # Remove auth header temporarily
        old = sync_client.headers.pop('X-Auth-Token', None)
        try:
            res = sync_client.post('/api/auth/logout')
            assert res.status_code == 200
        finally:
            if old:
                sync_client.headers['X-Auth-Token'] = old

    def test_error_format_uses_detail(self, app):
        """All 401 errors use {"detail": "..."} format."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            res = raw.get('/api/employees')
        body = res.json()
        assert 'detail' in body
        assert isinstance(body['detail'], str)
