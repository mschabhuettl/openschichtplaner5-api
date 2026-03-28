"""Tests for sp5lib.auto_migrate — automatic database migration on startup."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sp5lib.auto_migrate import (
    DBF_SCHEMA_VERSION,
    MigrationResult,
    _apply_dbf_extensions,
    _create_dbf_backup,
    _get_dbf_schema_version,
    _is_auto_backup_enabled,
    _is_auto_migrate_enabled,
    _set_dbf_schema_version,
    run_startup_migration,
)

# ── Environment flag tests ────────────────────────────────────


class TestAutoMigrateFlags:
    """Test AUTO_MIGRATE and AUTO_BACKUP environment variable handling."""

    def test_auto_migrate_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTO_MIGRATE", raising=False)
        assert _is_auto_migrate_enabled() is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "off"])
    def test_auto_migrate_disabled(self, monkeypatch, value):
        monkeypatch.setenv("AUTO_MIGRATE", value)
        assert _is_auto_migrate_enabled() is False

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes"])
    def test_auto_migrate_enabled_explicit(self, monkeypatch, value):
        monkeypatch.setenv("AUTO_MIGRATE", value)
        assert _is_auto_migrate_enabled() is True

    def test_auto_backup_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTO_BACKUP", raising=False)
        assert _is_auto_backup_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off"])
    def test_auto_backup_disabled(self, monkeypatch, value):
        monkeypatch.setenv("AUTO_BACKUP", value)
        assert _is_auto_backup_enabled() is False


# ── DBF version tracking tests ────────────────────────────────


class TestDbfVersionTracking:
    """Test DBF schema version file read/write."""

    def test_get_version_no_file(self, tmp_path):
        assert _get_dbf_schema_version(str(tmp_path)) is None

    def test_set_and_get_version(self, tmp_path):
        _set_dbf_schema_version(str(tmp_path), "1.2.3")
        assert _get_dbf_schema_version(str(tmp_path)) == "1.2.3"

    def test_overwrite_version(self, tmp_path):
        _set_dbf_schema_version(str(tmp_path), "1.0.0")
        _set_dbf_schema_version(str(tmp_path), "2.0.0")
        assert _get_dbf_schema_version(str(tmp_path)) == "2.0.0"


# ── DBF backup tests ─────────────────────────────────────────


class TestDbfBackup:
    """Test DBF directory backup."""

    def test_backup_nonexistent_path(self):
        result = _create_dbf_backup("/nonexistent/path/1234567890")
        assert result is None

    def test_backup_creates_copy(self, tmp_path):
        # Create a fake DBF directory with a file
        dbf_dir = tmp_path / "Daten"
        dbf_dir.mkdir()
        (dbf_dir / "5EMPL.DBF").write_text("fake dbf data")

        with patch("sp5lib.auto_migrate._BACKEND_DIR", tmp_path):
            result = _create_dbf_backup(str(dbf_dir))

        assert result is not None
        assert os.path.isdir(result)
        # Verify the file was copied
        copied_files = list(Path(result).glob("*.DBF"))
        assert len(copied_files) == 1
        assert copied_files[0].read_text() == "fake dbf data"


# ── DBF extension application tests ──────────────────────────


class TestDbfExtensions:
    """Test DBF schema extension application."""

    def test_no_extensions_to_apply(self, tmp_path):
        applied = _apply_dbf_extensions(str(tmp_path), DBF_SCHEMA_VERSION)
        assert applied == []

    def test_extensions_from_none(self, tmp_path):
        # No extensions registered for current version, so empty
        applied = _apply_dbf_extensions(str(tmp_path), None)
        assert applied == []

    def test_extensions_applied_in_order(self, tmp_path):
        """Test that extensions are applied in version order."""
        call_log = []

        def ext_1(db_path):
            call_log.append("1.1.0")

        def ext_2(db_path):
            call_log.append("1.2.0")

        extensions = {"1.2.0": [ext_2], "1.1.0": [ext_1]}
        with patch("sp5lib.auto_migrate._DBF_EXTENSIONS", extensions):
            applied = _apply_dbf_extensions(str(tmp_path), "1.0.0")

        assert applied == ["1.1.0", "1.2.0"]
        assert call_log == ["1.1.0", "1.2.0"]

    def test_extensions_skip_already_applied(self, tmp_path):
        """Test that extensions already at or below current version are skipped."""
        call_log = []

        def ext_1(db_path):
            call_log.append("1.1.0")

        extensions = {"1.1.0": [ext_1]}
        with patch("sp5lib.auto_migrate._DBF_EXTENSIONS", extensions):
            applied = _apply_dbf_extensions(str(tmp_path), "1.1.0")

        assert applied == []
        assert call_log == []


# ── MigrationResult tests ────────────────────────────────────


class TestMigrationResult:
    """Test MigrationResult data class."""

    def test_default_is_success(self):
        r = MigrationResult()
        assert r.success is True
        assert r.had_migrations is False

    def test_error_means_not_success(self):
        r = MigrationResult()
        r.error = "something broke"
        assert r.success is False

    def test_had_migrations(self):
        r = MigrationResult()
        r.migrations_applied = ["abc123"]
        assert r.had_migrations is True

    def test_to_dict(self):
        r = MigrationResult()
        r.backend = "dbf"
        r.skipped = True
        r.skip_reason = "already at current version"
        d = r.to_dict()
        assert d["backend"] == "dbf"
        assert d["skipped"] is True
        assert d["success"] is True

    def test_repr_skipped(self):
        r = MigrationResult()
        r.skipped = True
        r.skip_reason = "disabled"
        assert "skipped" in repr(r)

    def test_repr_error(self):
        r = MigrationResult()
        r.error = "boom"
        assert "error" in repr(r)

    def test_repr_normal(self):
        r = MigrationResult()
        r.backend = "dbf"
        r.previous_version = "1.0.0"
        r.current_version = "1.1.0"
        r.migrations_applied = ["1.1.0"]
        assert "1.0.0" in repr(r)


# ── Integration: run_startup_migration with DBF ──────────────


class TestRunStartupMigrationDbf:
    """Test run_startup_migration for DBF backend."""

    def test_skip_when_disabled(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "false")
        result = run_startup_migration()
        assert result.skipped is True
        assert "AUTO_MIGRATE" in result.skip_reason

    def test_dbf_stamps_version_on_fresh_db(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("AUTO_BACKUP", "false")
        monkeypatch.setenv("DB_BACKEND", "dbf")
        monkeypatch.setenv("SP5_DB_PATH", str(tmp_path))

        result = run_startup_migration()
        assert result.success is True
        assert result.backend == "dbf"
        assert result.current_version == DBF_SCHEMA_VERSION

        # Verify version file was written
        assert _get_dbf_schema_version(str(tmp_path)) == DBF_SCHEMA_VERSION

    def test_dbf_skips_when_up_to_date(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("DB_BACKEND", "dbf")
        monkeypatch.setenv("SP5_DB_PATH", str(tmp_path))
        _set_dbf_schema_version(str(tmp_path), DBF_SCHEMA_VERSION)

        result = run_startup_migration()
        assert result.skipped is True
        assert "already at current version" in result.skip_reason

    def test_dbf_nonexistent_path(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("DB_BACKEND", "dbf")
        monkeypatch.setenv("SP5_DB_PATH", "/nonexistent/path/1234567890")

        result = run_startup_migration()
        assert result.skipped is True
        assert "not found" in result.skip_reason

    def test_dbf_with_backup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("AUTO_BACKUP", "true")
        monkeypatch.setenv("DB_BACKEND", "dbf")
        # Use separate dirs for db_path and backend_dir to avoid recursive copytree
        dbf_dir = tmp_path / "dbdata"
        dbf_dir.mkdir()
        (dbf_dir / "5EMPL.DBF").write_text("test")
        monkeypatch.setenv("SP5_DB_PATH", str(dbf_dir))

        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        with patch("sp5lib.auto_migrate._BACKEND_DIR", backend_dir):
            result = run_startup_migration()

        assert result.success is True
        assert result.backup_path is not None


# ── Integration: run_startup_migration with PostgreSQL ───────


class TestRunStartupMigrationPg:
    """Test run_startup_migration for PostgreSQL backend (mocked)."""

    def test_pg_no_database_url(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("DB_BACKEND", "postgresql")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        result = run_startup_migration()
        assert result.skipped is True
        assert "DATABASE_URL" in result.skip_reason

    def test_pg_already_at_head(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("DB_BACKEND", "postgresql")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/sp5test")

        with patch("sp5lib.auto_migrate._get_db_revision", return_value="abc123"), \
             patch("sp5lib.auto_migrate._get_alembic_head", return_value="abc123"):
            result = run_startup_migration()

        assert result.skipped is True
        assert "already at head" in result.skip_reason

    def test_pg_migration_needed(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("AUTO_BACKUP", "false")
        monkeypatch.setenv("DB_BACKEND", "postgresql")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/sp5test")

        with patch("sp5lib.auto_migrate._get_db_revision", side_effect=["old_rev", "new_rev"]), \
             patch("sp5lib.auto_migrate._get_alembic_head", return_value="new_rev"), \
             patch("sp5lib.auto_migrate._run_alembic_upgrade", return_value=["new_rev"]):
            result = run_startup_migration()

        assert result.success is True
        assert result.backend == "postgresql"
        assert result.migrations_applied == ["new_rev"]

    def test_pg_migration_failure(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        monkeypatch.setenv("AUTO_BACKUP", "false")
        monkeypatch.setenv("DB_BACKEND", "postgresql")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/sp5test")

        with patch("sp5lib.auto_migrate._get_db_revision", return_value=None), \
             patch("sp5lib.auto_migrate._get_alembic_head", return_value="abc123"), \
             patch("sp5lib.auto_migrate._run_alembic_upgrade", side_effect=RuntimeError("DB down")):
            result = run_startup_migration()

        assert result.success is False
        assert "DB down" in result.error


# ── API endpoint test ─────────────────────────────────────────


class TestMigrationStatusEndpoint:
    """Test the /api/migration/status endpoint."""

    @pytest.fixture
    def client(self):
        from api.main import app
        from fastapi.testclient import TestClient
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def admin_token(self):
        from tests.conftest import _inject_token
        return _inject_token("Admin", "TestAdmin")

    def test_migration_status_requires_auth(self, client):
        resp = client.get("/api/migration/status")
        assert resp.status_code == 401

    def test_migration_status_returns_data(self, client, admin_token):
        resp = client.get(
            "/api/migration/status",
            headers={"x-auth-token": admin_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "backend" in data
        assert "auto_migrate_enabled" in data
        assert "up_to_date" in data
