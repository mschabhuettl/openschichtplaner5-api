"""
Shared test fixtures for OpenSchichtplaner5 backend tests.
"""
import os
import sys
import shutil
import tempfile
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

# ── Mock user factories ────────────────────────────────────────────────────────

def _mock_admin():
    return {'ID': 1, 'NAME': 'admin', 'ADMIN': True, 'role': 'Admin'}

def _mock_planer():
    return {'ID': 2, 'NAME': 'planer', 'ADMIN': False, 'role': 'Planer'}

def _mock_leser():
    return {'ID': 3, 'NAME': 'leser', 'ADMIN': False, 'role': 'Leser'}


# ── Helper: patch all auth deps to a given mock ────────────────────────────────

def _patch_auth(app, user_fn):
    """Override all auth dependencies to return user_fn(). Returns previous overrides."""
    from api.main import require_auth, require_admin, require_planer, get_current_user
    prev = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = user_fn
    app.dependency_overrides[require_auth] = user_fn
    app.dependency_overrides[require_admin] = user_fn
    app.dependency_overrides[require_planer] = user_fn
    return prev

def _restore_auth(app, prev):
    """Restore dependency overrides to prev state."""
    app.dependency_overrides.clear()
    app.dependency_overrides.update(prev)


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


@pytest.fixture(scope="session")
def sync_client(app):
    """
    Session-scoped synchronous TestClient with admin auth bypass.
    Uses a real login token so it doesn't conflict with function-scoped fixtures.
    """
    from starlette.testclient import TestClient
    # Use real login to get a token
    with TestClient(app, raise_server_exceptions=True) as c:
        res = c.post('/api/auth/login', json={'username': 'admin', 'password': 'Test1234'})
        if res.status_code == 200:
            token = res.json()['token']
            # Set default auth header for all requests via requests.Session headers
            c.headers['X-Auth-Token'] = token
        yield c


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


def _authed_client(app, user_fn, raise_exc=True):
    """Create a TestClient with a specific role auth bypass. Context manager friendly."""
    from starlette.testclient import TestClient
    prev = _patch_auth(app, user_fn)
    client = TestClient(app, raise_server_exceptions=raise_exc)
    client.__enter__()
    return client, prev


@pytest.fixture
def write_client(write_db_path, app):
    """Function-scoped sync TestClient with admin auth bypass and fresh DB."""
    from starlette.testclient import TestClient
    prev = _patch_auth(app, _mock_admin)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _restore_auth(app, prev)


@pytest.fixture
def admin_client(write_db_path, app):
    """Function-scoped sync TestClient with admin auth bypass and fresh DB."""
    from starlette.testclient import TestClient
    prev = _patch_auth(app, _mock_admin)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _restore_auth(app, prev)


@pytest.fixture
def planer_client(write_db_path, app):
    """Function-scoped TestClient with Planer role. require_admin still raises 403."""
    from starlette.testclient import TestClient
    from api.main import require_auth, require_planer, get_current_user
    prev = dict(app.dependency_overrides)
    # Override only auth+planer, NOT require_admin (so admin endpoints stay 403)
    app.dependency_overrides[get_current_user] = _mock_planer
    app.dependency_overrides[require_auth] = _mock_planer
    app.dependency_overrides[require_planer] = _mock_planer
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    _restore_auth(app, prev)


@pytest.fixture
def leser_client(write_db_path, app):
    """Function-scoped TestClient with Leser role. Only require_auth bypassed."""
    from starlette.testclient import TestClient
    from api.main import require_auth, get_current_user
    prev = dict(app.dependency_overrides)
    # Override only auth, NOT require_planer or require_admin
    app.dependency_overrides[get_current_user] = _mock_leser
    app.dependency_overrides[require_auth] = _mock_leser
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    _restore_auth(app, prev)
