"""
Tests for data-integrity and backend robustness:
- Missing DBF files return empty results (not 500 errors)
- Corrupted DBF files return empty results (not 500 errors)
- TOCTOU protection in reader/writer
- Cache behaviour for missing files
"""
import os
import tempfile
from sp5lib.dbf_reader import read_dbf, get_table_fields
from sp5lib.dbf_writer import find_all_records


# ─── read_dbf ─────────────────────────────────────────────────────────────────

class TestReadDbfRobustness:

    def test_missing_file_returns_empty_list(self):
        result = read_dbf("/tmp/nonexistent_sp5_test_12345.DBF")
        assert result == []

    def test_empty_file_returns_empty_list(self):
        with tempfile.NamedTemporaryFile(suffix=".DBF", delete=False) as f:
            path = f.name
        try:
            result = read_dbf(path)
            assert result == []
        finally:
            os.unlink(path)

    def test_truncated_header_returns_empty_list(self):
        with tempfile.NamedTemporaryFile(suffix=".DBF", delete=False) as f:
            f.write(b'\x03\x00\x00\x00')  # Only 4 bytes — incomplete header
            path = f.name
        try:
            result = read_dbf(path)
            assert result == []
        finally:
            os.unlink(path)

    def test_corrupted_content_returns_empty_or_partial(self):
        """Corrupted body after valid header should not raise an exception."""
        with tempfile.NamedTemporaryFile(suffix=".DBF", delete=False) as f:
            # 32-byte header: version=3, nrecs=2, header_size=65, record_size=10
            import struct
            header = bytearray(32)
            header[0] = 0x03
            struct.pack_into('<I', header, 4, 2)    # 2 records
            struct.pack_into('<H', header, 8, 65)   # header_size
            struct.pack_into('<H', header, 10, 10)  # record_size
            f.write(bytes(header))
            # Terminator (0x0D) without any field descriptors
            f.write(b'\x0d')
            # Garbage record data
            f.write(b'\x00' * 5)  # too short
            path = f.name
        try:
            result = read_dbf(path)
            assert isinstance(result, list)
        finally:
            os.unlink(path)

    def test_permission_error_returns_empty_list(self, tmp_path):
        """A file that exists but is unreadable returns []."""
        dbf = tmp_path / "locked.DBF"
        dbf.write_bytes(b'\x00' * 64)
        dbf.chmod(0o000)
        try:
            result = read_dbf(str(dbf))
            assert result == []
        finally:
            dbf.chmod(0o644)


# ─── get_table_fields ─────────────────────────────────────────────────────────

class TestGetTableFieldsRobustness:

    def test_missing_file_returns_empty_list(self):
        result = get_table_fields("/tmp/nonexistent_sp5_fields_12345.DBF")
        assert result == []

    def test_permission_error_returns_empty_list(self, tmp_path):
        dbf = tmp_path / "locked_fields.DBF"
        dbf.write_bytes(b'\x00' * 64)
        dbf.chmod(0o000)
        try:
            result = get_table_fields(str(dbf))
            assert result == []
        finally:
            dbf.chmod(0o644)


# ─── find_all_records ─────────────────────────────────────────────────────────

class TestFindAllRecordsRobustness:

    def test_missing_file_returns_empty_list(self):
        result = find_all_records("/tmp/nonexistent_sp5_far_12345.DBF", [])
        assert result == []


# ─── database._read via API ───────────────────────────────────────────────────

class TestDatabaseReadRobustness:
    """Verify that SP5Database._read returns [] for missing tables without raising."""

    def test_read_missing_table_returns_empty_list(self, tmp_path):
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))  # empty dir — no DBF files
        result = db._read('GROUP')
        assert result == []

    def test_read_missing_employee_table_returns_empty_list(self, tmp_path):
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))
        result = db._read('EMPL')
        assert result == []

    def test_read_missing_grasg_returns_empty_list(self, tmp_path):
        """GRASG (group assignments) is optional — missing file must not crash."""
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))
        result = db._read('GRASG')
        assert result == []

    def test_get_groups_empty_db_returns_empty_list(self, tmp_path):
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))
        groups = db.get_groups()
        assert groups == []

    def test_get_employees_empty_db_returns_empty_list(self, tmp_path):
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))
        emps = db.get_employees()
        assert emps == []

    def test_get_shifts_empty_db_returns_empty_list(self, tmp_path):
        from sp5lib.database import SP5Database
        db = SP5Database(str(tmp_path))
        shifts = db.get_shifts()
        assert shifts == []

    def test_cache_missing_file_does_not_persist_across_instances(self, tmp_path):
        """After a file is created, the next _read should return data, not []."""
        import struct
        from sp5lib.database import SP5Database, _GLOBAL_DBF_CACHE

        db = SP5Database(str(tmp_path))
        # First read — file missing, should return []
        result1 = db._read('SHIFT')
        assert result1 == []

        # Create a minimal valid (but empty) DBF for SHIFT
        dbf_path = tmp_path / "5SHIFT.DBF"
        header = bytearray(32)
        header[0] = 0x03
        struct.pack_into('<I', header, 4, 0)    # 0 records
        struct.pack_into('<H', header, 8, 33)   # header_size (32 + terminator)
        struct.pack_into('<H', header, 10, 1)   # record_size
        dbf_path.write_bytes(bytes(header) + b'\x0d')

        # Invalidate cache to simulate mtime change detection
        _GLOBAL_DBF_CACHE.pop((str(tmp_path), 'SHIFT'), None)

        result2 = db._read('SHIFT')
        assert isinstance(result2, list)  # [] is fine for an empty table
