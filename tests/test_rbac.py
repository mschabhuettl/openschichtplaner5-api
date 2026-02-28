"""Tests for RBAC: Leser/Planer/Admin permissions on protected endpoints."""
import secrets
import pytest
from starlette.testclient import TestClient


# ── Role injection helpers ─────────────────────────────────────────────────────

def _inject_token(role: str, name: str) -> str:
    """Inject a session token with the given role into _sessions and return it."""
    from api.main import _sessions
    tok = secrets.token_hex(16)
    _sessions[tok] = {'ID': 900, 'NAME': name, 'role': role, 'ADMIN': role == 'Admin', 'RIGHTS': 0}
    return tok


def _remove_token(tok: str) -> None:
    from api.main import _sessions
    _sessions.pop(tok, None)


def _h(token: str) -> dict:
    return {'X-Auth-Token': token}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_token():
    tok = _inject_token('Admin', 'test_admin')
    yield tok
    _remove_token(tok)


@pytest.fixture
def planer_token():
    tok = _inject_token('Planer', 'test_planer')
    yield tok
    _remove_token(tok)


@pytest.fixture
def leser_token():
    tok = _inject_token('Leser', 'test_leser')
    yield tok
    _remove_token(tok)


# ── Unauthenticated ────────────────────────────────────────────────────────────

class TestUnauthenticated:
    def test_employees_requires_auth(self, app):
        """Verify employees requires auth."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            assert raw.get('/api/employees').status_code == 401

    def test_schedule_requires_auth(self, app):
        """Verify schedule requires auth."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            assert raw.get('/api/schedule?year=2024&month=1').status_code == 401

    def test_users_requires_auth(self, app):
        """Verify users requires auth."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            assert raw.get('/api/users').status_code == 401

    def test_401_error_has_detail(self, app):
        """Verify 401 error has detail."""
        with TestClient(app, raise_server_exceptions=False) as raw:
            res = raw.get('/api/employees')
        assert 'detail' in res.json()


# ── Leser (read-only) ──────────────────────────────────────────────────────────

class TestLeserPermissions:
    def test_can_read_employees(self, sync_client, leser_token):
        """Verify can read employees."""
        res = sync_client.get('/api/employees', headers=_h(leser_token))
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_can_read_shifts(self, sync_client, leser_token):
        """Verify can read shifts."""
        assert sync_client.get('/api/shifts', headers=_h(leser_token)).status_code == 200

    def test_can_read_schedule(self, sync_client, leser_token):
        """Verify can read schedule."""
        res = sync_client.get('/api/schedule?year=2024&month=1', headers=_h(leser_token))
        assert res.status_code == 200

    def test_cannot_create_employee(self, sync_client, leser_token):
        """POST /api/employees requires Admin."""
        res = sync_client.post('/api/employees', json={'NAME': 'X'}, headers=_h(leser_token))
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}: {res.text}"

    def test_cannot_create_schedule(self, sync_client, leser_token):
        """POST /api/schedule requires Planer."""
        res = sync_client.post(
            '/api/schedule',
            json={'employee_id': 1, 'date': '2024-01-15', 'shift_id': 1},
            headers=_h(leser_token),
        )
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}"

    def test_cannot_create_user(self, sync_client, leser_token):
        """POST /api/users requires Admin."""
        res = sync_client.post(
            '/api/users',
            json={'NAME': 'newuser', 'PASSWORD': 'pass123', 'role': 'Leser'},
            headers=_h(leser_token),
        )
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}"

    def test_cannot_delete_employee(self, sync_client, leser_token):
        """DELETE /api/employees requires Admin."""
        res = sync_client.delete('/api/employees/9999', headers=_h(leser_token))
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}"


# ── Planer ─────────────────────────────────────────────────────────────────────

class TestPlanerPermissions:
    def test_can_read_employees(self, sync_client, planer_token):
        """Verify can read employees."""
        assert sync_client.get('/api/employees', headers=_h(planer_token)).status_code == 200

    def test_cannot_create_employee(self, sync_client, planer_token):
        """POST /api/employees requires Admin."""
        res = sync_client.post('/api/employees', json={'NAME': 'X'}, headers=_h(planer_token))
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}: {res.text}"

    def test_cannot_create_user(self, sync_client, planer_token):
        """POST /api/users requires Admin."""
        res = sync_client.post(
            '/api/users',
            json={'NAME': 'newplaner', 'PASSWORD': 'pass123', 'role': 'Planer'},
            headers=_h(planer_token),
        )
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}"

    def test_can_add_note(self, sync_client, planer_token):
        """Notes require Planer or higher — should not be 401/403."""
        res = sync_client.post(
            '/api/notes',
            json={'date': '2024-06-01', 'text': 'Test note', 'employee_id': 0},
            headers=_h(planer_token),
        )
        assert res.status_code not in (401, 403), "Planer should be able to add notes"


# ── Admin ──────────────────────────────────────────────────────────────────────

class TestAdminPermissions:
    def test_can_access_users(self, sync_client, admin_token):
        """Verify can access users."""
        res = sync_client.get('/api/users', headers=_h(admin_token))
        assert res.status_code == 200

    def test_can_read_employees(self, sync_client, admin_token):
        """Verify can read employees."""
        assert sync_client.get('/api/employees', headers=_h(admin_token)).status_code == 200

    def test_can_create_user(self, write_client, write_db_path):
        """Admin can create a new user."""
        tok = _inject_token('Admin', 'write_admin')
        try:
            unique_name = f'testuser_{secrets.token_hex(4)}'
            res = write_client.post(
                '/api/users',
                json={'NAME': unique_name, 'PASSWORD': 'Test1234!', 'role': 'Leser'},
                headers=_h(tok),
            )
            assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
            assert res.json()['ok'] is True
        finally:
            _remove_token(tok)

    def test_403_has_detail_format(self, sync_client, leser_token):
        """403 errors use {"detail": "..."} format."""
        res = sync_client.post('/api/employees', json={'NAME': 'X'}, headers=_h(leser_token))
        assert res.status_code in (401, 403)
        body = res.json()
        assert 'detail' in body
        assert isinstance(body['detail'], str)
