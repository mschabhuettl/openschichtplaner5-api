"""
Tests for dev-mode token guard and /api/dev/mode endpoint.
"""
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client_no_dev(monkeypatch):
    """TestClient with SP5_DEV_MODE NOT active."""
    import api.dependencies as deps
    import api.main as main_mod
    monkeypatch.setattr(deps, '_DEV_MODE_ACTIVE', False)
    monkeypatch.setattr(main_mod, '_DEV_MODE_ACTIVE', False)
    # Remove dev token from sessions to simulate production
    deps._sessions.pop('__dev_mode__', None)
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_dev(monkeypatch):
    """TestClient with SP5_DEV_MODE=true."""
    import api.dependencies as deps
    import api.main as main_mod
    monkeypatch.setattr(deps, '_DEV_MODE_ACTIVE', True)
    monkeypatch.setattr(main_mod, '_DEV_MODE_ACTIVE', True)
    # Ensure dev token is in sessions (as main.py does on startup)
    deps._sessions['__dev_mode__'] = {**deps._DEV_USER, 'expires_at': None}
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestDevModeGuard:
    def test_dev_mode_token_rejected_when_not_dev(self, client_no_dev):
        """__dev_mode__ token must be rejected when SP5_DEV_MODE is not active."""
        r = client_no_dev.get('/api/auth/me', headers={'X-Auth-Token': '__dev_mode__'})
        assert r.status_code == 401

    def test_dev_mode_token_accepted_when_dev(self, client_dev):
        """__dev_mode__ token is accepted when SP5_DEV_MODE is active."""
        r = client_dev.get('/api/auth/me', headers={'X-Auth-Token': '__dev_mode__'})
        assert r.status_code == 200
        data = r.json()
        assert data.get('role') == 'Admin'
        assert data.get('NAME') == 'Developer'

    def test_dev_mode_endpoint_returns_false_when_not_dev(self, client_no_dev):
        r = client_no_dev.get('/api/dev/mode')
        assert r.status_code == 200
        assert r.json()['dev_mode'] is False

    def test_dev_mode_endpoint_returns_true_when_dev(self, client_dev):
        r = client_dev.get('/api/dev/mode')
        assert r.status_code == 200
        assert r.json()['dev_mode'] is True

    def test_random_token_rejected(self, client_no_dev):
        r = client_no_dev.get('/api/auth/me', headers={'X-Auth-Token': 'some-random-token'})
        assert r.status_code == 401

    def test_dev_mode_endpoint_no_auth_required(self, client_no_dev):
        """Anyone can call /api/dev/mode to check if dev mode is on."""
        r = client_no_dev.get('/api/dev/mode')
        assert r.status_code == 200
