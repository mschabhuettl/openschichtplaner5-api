"""
Shared test fixtures for OpenSchichtplaner5 backend tests.
"""
import os
import sys
import shutil
import tempfile
import pytest

# ── Python path setup ──────────────────────────────────────────────────────────
# Ensure the backend directory is importable
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_SITE_PACKAGES = os.path.join(_BACKEND_DIR, "venv", "lib", "python3.13", "site-packages")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if os.path.isdir(_VENV_SITE_PACKAGES) and _VENV_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_SITE_PACKAGES)

# ── Real DBF data source ───────────────────────────────────────────────────────
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Use SP5_REAL_DB env var → real DB → bundled fixtures (in that order)
_REAL_DB_PATH = os.environ.get("SP5_REAL_DB") or (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else _FIXTURES_DIR
)


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """
    Session-scoped fixture: copies the real SP5 database to a temp directory.
    All tests in a session share this copy, so write-tests should use
    function-scoped copies if they need isolation.
    """
    base = tmp_path_factory.mktemp("sp5_db")
    dst = base / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    return str(dst)


@pytest.fixture(scope="session")
def patched_db(test_db_path):
    """
    Session-scoped fixture: sets SP5_DB_PATH env var AND patches api.main.DB_PATH
    so TestClient requests go to the test database.
    Returns the test DB path.
    """
    os.environ["SP5_DB_PATH"] = test_db_path

    # Import + patch the module-level DB_PATH
    import api.main as main_module
    original = main_module.DB_PATH
    main_module.DB_PATH = test_db_path
    yield test_db_path
    # Restore
    main_module.DB_PATH = original


@pytest.fixture(scope="session")
def app(patched_db):
    """Return the FastAPI app pointed at the test database."""
    from api.main import app as _app
    return _app


@pytest.fixture(scope="session")
def sync_client(app):
    """
    Session-scoped synchronous TestClient (from Starlette).
    Good for read-only tests and simple assertions.
    """
    from starlette.testclient import TestClient
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def write_db_path(tmp_path):
    """
    Function-scoped fixture for write tests: fresh DB copy per test.
    Patches api.main.DB_PATH for the duration of the test.
    """
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
    """
    Function-scoped sync TestClient for write/mutation tests.
    Uses a fresh DB copy each time.
    """
    from starlette.testclient import TestClient
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def admin_client(write_db_path, app):
    """
    Function-scoped sync TestClient with admin auth bypassed.
    Overrides the require_admin dependency to always return a mock admin user.
    Uses a fresh DB copy each time.
    """
    from starlette.testclient import TestClient
    from api.main import require_admin

    def mock_admin():
        return {'ID': 1, 'NAME': 'admin', 'ADMIN': True, 'role': 'Admin'}

    app.dependency_overrides[require_admin] = mock_admin
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_admin, None)
