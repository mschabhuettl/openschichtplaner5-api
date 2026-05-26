"""Unit tests for the admin backup helpers — zip creation with the critical-file
integrity check, retention rotation, and the auto-backup skip/create logic.
These are data-safety paths; they're driven with temp dirs and SP5_DB_PATH."""

import io
import os
import zipfile

import api.routers.admin as admin
import pytest
from fastapi import HTTPException


def _make_db_dir(tmp_path, complete=True):
    d = tmp_path / "Daten"
    d.mkdir()
    files = set(admin._BACKUP_REQUIRED_FILES)
    if not complete:
        files.discard("5EMPL.DBF")  # leave a critical file out
    for f in files:
        (d / f).write_bytes(b"data")
    return str(d)


class TestCreateZipBytes:
    def test_missing_critical_files_raises_500(self, tmp_path):
        d = _make_db_dir(tmp_path, complete=False)
        with pytest.raises(HTTPException) as exc:
            admin._create_zip_bytes(d)
        assert exc.value.status_code == 500

    def test_creates_valid_zip(self, tmp_path):
        d = _make_db_dir(tmp_path, complete=True)
        data = admin._create_zip_bytes(d)
        assert data[:2] == b"PK"  # zip magic bytes
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert "5EMPL.DBF" in names


class TestRotateBackups:
    def test_keeps_only_newest(self, tmp_path):
        d = tmp_path / "backups"
        d.mkdir()
        n = admin._BACKUP_MAX_COUNT + 3
        for i in range(n):
            (d / f"sp5_backup_2026010{i}_00000{i}.zip").write_bytes(b"x")
        (d / "keep_me.txt").write_bytes(b"x")  # non-backup file untouched
        admin._rotate_backups(str(d))
        remaining = [f for f in os.listdir(d) if f.startswith("sp5_backup_")]
        assert len(remaining) == admin._BACKUP_MAX_COUNT
        assert (d / "keep_me.txt").exists()


class TestCreateAutoBackup:
    def test_skips_when_no_db_path(self, monkeypatch):
        monkeypatch.setenv("SP5_DB_PATH", "")
        assert admin.create_auto_backup() is None

    def test_skips_when_recent_backup_exists(self, tmp_path, monkeypatch):
        d = _make_db_dir(tmp_path, complete=True)
        monkeypatch.setenv("SP5_DB_PATH", d)
        bdir = os.path.join(os.path.dirname(d), "backups")
        os.makedirs(bdir, exist_ok=True)
        # a freshly-written backup → age < 24h → skip
        with open(os.path.join(bdir, "sp5_backup_20260101_000000.zip"), "wb") as f:
            f.write(b"x")
        assert admin.create_auto_backup() is None

    def test_creates_backup_when_none_recent(self, tmp_path, monkeypatch):
        d = _make_db_dir(tmp_path, complete=True)
        monkeypatch.setenv("SP5_DB_PATH", d)
        fn = admin.create_auto_backup()
        assert fn is not None
        assert fn.startswith("sp5_backup_") and fn.endswith(".zip")
        bdir = os.path.join(os.path.dirname(d), "backups")
        assert os.path.exists(os.path.join(bdir, fn))
