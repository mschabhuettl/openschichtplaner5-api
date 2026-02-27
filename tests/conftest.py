"""
Shared test fixtures for OpenSchichtplaner5 backend tests.
"""
import os
import sys
import secrets
import shutil
import pytest

# ── Python path setup ──────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_SITE_PACKAGES = os.path.join(_BACKEND_DIR, "venv", "lib", "python3.13", "site-packages")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if os.path.isdir(_VENV_SITE_PACKAGES) and _VENV_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_SITE_PACKAGES)

# ── Real DBF data source ───────────────────────────────────────────────────────
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_REAL_DB_PATH = os.environ.get("SP5_REAL_DB") or (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else _FIXTURES_DIR
)

# ── Session token helpers ──────────────────────────────────────────────────────

def _inject_token(role: str, name: str = None) -> str:
    """Inject a session token with the given role. Returns the token."""
    from api.main import _sessions
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        'ID': 900 + hash(role) % 10,
        'NAME': name or f'test_{role.lower()}',
        'role': role,
        'ADMIN': role == 'Admin',
        'RIGHTS': 255 if role == 'Admin' else (2 if role == 'Planer' else 1),
    }
    return tok


def _remove_token(tok: str) -> None:
    from api.main import _sessions
    _sessions.pop(tok, None)


def _auth_headers(tok: str) -> dict:
    return {'X-Auth-Token': tok}


# ── DB fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """Session-scoped: copies real SP5 DB to temp dir."""
    base = tmp_path_factory.mktemp("sp5_db")
    dst = base / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    return str(dst)


@pytest.fixture(scope="session")
def patched_db(test_db_path):
    """Session-scoped: sets SP5_DB_PATH and patches api.main.DB_PATH."""
    os.environ["SP5_DB_PATH"] = test_db_path
    import api.main as main_module
    original = main_module.DB_PATH
    main_module.DB_PATH = test_db_path
    yield test_db_path
    main_module.DB_PATH = original


@pytest.fixture(scope="session")
def app(patched_db):
    """Return the FastAPI app pointed at the test database."""
    from api.main import app as _app
    return _app


# ── Client fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def sync_client(app):
    """Session-scoped TestClient with injected admin session token.
    The token is kept alive by re-injecting it into _sessions on each request
    to survive potential logout calls from other tests.
    """
    from starlette.testclient import TestClient
    from api.main import _sessions

    tok = 'SYNC_CLIENT_PERSISTENT_ADMIN_TOKEN'
    _sessions[tok] = {
        'ID': 999,
        'NAME': 'sync_admin',
        'role': 'Admin',
        'ADMIN': True,
        'RIGHTS': 255,
    }

    class PersistentTokenClient(TestClient):
        """TestClient that re-injects the token before each request."""
        def request(self, method, url, **kwargs):
            # Re-inject token in case a logout test removed it
            if tok not in _sessions:
                _sessions[tok] = {
                    'ID': 999,
                    'NAME': 'sync_admin',
                    'role': 'Admin',
                    'ADMIN': True,
                    'RIGHTS': 255,
                }
            return super().request(method, url, **kwargs)

    with PersistentTokenClient(app, raise_server_exceptions=True) as c:
        c.headers['X-Auth-Token'] = tok
        yield c

    _sessions.pop(tok, None)


@pytest.fixture
def write_db_path(tmp_path):
    """Function-scoped: fresh DB copy per test."""
    dst = tmp_path / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    db_path = str(dst)

    import api.main as main_module
    original = main_module.DB_PATH
    main_module.DB_PATH = db_path
    os.environ["SP5_DB_PATH"] = db_path
    yield db_path
    main_module.DB_PATH = original


@pytest.fixture
def write_client(write_db_path, app):
    """Function-scoped TestClient with admin token and fresh DB copy."""
    from starlette.testclient import TestClient
    tok = _inject_token('Admin', 'write_admin')
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            c.headers['X-Auth-Token'] = tok
            yield c
    finally:
        _remove_token(tok)


@pytest.fixture
def admin_client(write_db_path, app):
    """Function-scoped TestClient with Admin role and fresh DB copy."""
    from starlette.testclient import TestClient
    tok = _inject_token('Admin', 'fixture_admin')
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            c.headers['X-Auth-Token'] = tok
            yield c
    finally:
        _remove_token(tok)


@pytest.fixture
def planer_client(write_db_path, app):
    """Function-scoped TestClient with Planer role."""
    from starlette.testclient import TestClient
    tok = _inject_token('Planer', 'fixture_planer')
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers['X-Auth-Token'] = tok
            yield c
    finally:
        _remove_token(tok)


@pytest.fixture
def leser_client(write_db_path, app):
    """Function-scoped TestClient with Leser role."""
    from starlette.testclient import TestClient
    tok = _inject_token('Leser', 'fixture_leser')
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers['X-Auth-Token'] = tok
            yield c
    finally:
        _remove_token(tok)
