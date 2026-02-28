"""
Edge case tests for OpenSchichtplaner5 backend.
Covers: invalid month/year bounds, missing DB files, empty results, long strings.
"""
import os
import pytest


# ── Month/Year bounds validation ──────────────────────────────────────────────

class TestMonthYearBounds:
    """Verify all endpoints reject out-of-range month/year values."""

    def test_schedule_month_too_high(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024&month=13")
        assert resp.status_code == 400

    def test_schedule_month_zero(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024&month=0")
        assert resp.status_code == 400

    def test_schedule_month_negative(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024&month=-1")
        assert resp.status_code == 400

    def test_schedule_year_too_low(self, sync_client):
        resp = sync_client.get("/api/schedule?year=1999&month=6")
        assert resp.status_code == 400

    def test_schedule_year_too_high(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2101&month=6")
        assert resp.status_code == 400

    def test_staffing_month_invalid(self, sync_client):
        resp = sync_client.get("/api/staffing?year=2024&month=13")
        assert resp.status_code == 400

    def test_staffing_month_zero(self, sync_client):
        resp = sync_client.get("/api/staffing?year=2024&month=0")
        assert resp.status_code == 400

    def test_staffing_year_too_low(self, sync_client):
        resp = sync_client.get("/api/staffing?year=1800&month=6")
        assert resp.status_code == 400

    def test_staffing_year_too_high(self, sync_client):
        resp = sync_client.get("/api/staffing?year=9999&month=6")
        assert resp.status_code == 400

    def test_schedule_coverage_month_invalid(self, sync_client):
        resp = sync_client.get("/api/schedule/coverage?year=2024&month=13")
        assert resp.status_code == 400

    def test_schedule_coverage_year_invalid(self, sync_client):
        resp = sync_client.get("/api/schedule/coverage?year=1999&month=6")
        assert resp.status_code == 400

    def test_schedule_conflicts_month_invalid(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2024&month=0")
        assert resp.status_code == 400

    def test_schedule_conflicts_year_invalid(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2200&month=6")
        assert resp.status_code == 400

    def test_schedule_year_year_invalid(self, sync_client):
        resp = sync_client.get("/api/schedule/year?year=1990&employee_id=1")
        assert resp.status_code == 400

    def test_burnout_radar_month_invalid(self, sync_client):
        resp = sync_client.get("/api/burnout-radar?year=2024&month=13")
        assert resp.status_code == 400

    def test_burnout_radar_month_zero(self, sync_client):
        resp = sync_client.get("/api/burnout-radar?year=2024&month=0")
        assert resp.status_code == 400

    def test_burnout_radar_year_invalid(self, sync_client):
        resp = sync_client.get("/api/burnout-radar?year=1900&month=6")
        assert resp.status_code == 400

    def test_schedule_valid_boundary_month_1(self, sync_client):
        """Month=1 should be valid (no 400)."""
        resp = sync_client.get("/api/schedule?year=2024&month=1")
        assert resp.status_code != 400

    def test_schedule_valid_boundary_month_12(self, sync_client):
        """Month=12 should be valid (no 400)."""
        resp = sync_client.get("/api/schedule?year=2024&month=12")
        assert resp.status_code != 400

    def test_schedule_valid_boundary_year_2000(self, sync_client):
        """Year=2000 should be valid (no 400)."""
        resp = sync_client.get("/api/schedule?year=2000&month=6")
        assert resp.status_code != 400

    def test_schedule_valid_boundary_year_2100(self, sync_client):
        """Year=2100 should be valid (no 400)."""
        resp = sync_client.get("/api/schedule?year=2100&month=6")
        assert resp.status_code != 400


# ── DBF reader edge cases ─────────────────────────────────────────────────────

class TestDbfReaderEdgeCases:
    """Verify DBF reader handles missing/corrupt files gracefully."""

    def test_read_dbf_missing_file_returns_empty(self):
        from sp5lib.dbf_reader import read_dbf
        result = read_dbf("/nonexistent/path/MISSING.DBF")
        assert result == []

    def test_get_table_fields_missing_file_returns_empty(self):
        from sp5lib.dbf_reader import get_table_fields
        result = get_table_fields("/nonexistent/path/MISSING.DBF")
        assert result == []

    def test_read_dbf_empty_path_returns_empty(self):
        from sp5lib.dbf_reader import read_dbf
        result = read_dbf("")
        assert result == []

    def test_read_dbf_truncated_file_returns_empty(self, tmp_path):
        from sp5lib.dbf_reader import read_dbf
        f = tmp_path / "truncated.dbf"
        f.write_bytes(b'\x03\x00')  # too short (< 32 bytes)
        result = read_dbf(str(f))
        assert result == []

    def test_find_all_records_missing_file_returns_empty(self):
        from sp5lib.dbf_writer import find_all_records
        result = find_all_records("/nonexistent/path/MISSING.DBF", fields=[])
        assert result == []

    def test_read_header_info_missing_file_raises(self):
        from sp5lib.dbf_writer import _read_header_info
        with pytest.raises(FileNotFoundError) as exc_info:
            _read_header_info("/nonexistent/MISSING.DBF")
        assert "DBF-Datei nicht gefunden" in str(exc_info.value)
        assert "MISSING.DBF" in str(exc_info.value)


# ── Empty result handling ─────────────────────────────────────────────────────

class TestEmptyResults:
    """Endpoints should return empty lists/dicts, never None."""

    def test_schedule_empty_month_returns_list(self, sync_client):
        """Remote future month returns a list (possibly empty), not None."""
        resp = sync_client.get("/api/schedule?year=2099&month=6")
        assert resp.status_code == 200
        data = resp.json()
        # Should be a list or dict, never null
        assert data is not None

    def test_staffing_empty_returns_not_none(self, sync_client):
        resp = sync_client.get("/api/staffing?year=2099&month=6")
        assert resp.status_code == 200
        assert resp.json() is not None

    def test_schedule_conflicts_empty_returns_dict(self, sync_client):
        resp = sync_client.get("/api/schedule/conflicts?year=2099&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert data is not None
        assert "conflicts" in data

    def test_employees_empty_db_returns_list(self, tmp_path):
        """Database with 0 employees returns empty list, not None."""
        import shutil

        fixtures_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        dst = tmp_path / "Daten"
        shutil.copytree(fixtures_dir, str(dst))

        import api.main as main_module
        from sp5lib.database import SP5Database
        old_db_path = main_module.DB_PATH

        try:
            main_module.DB_PATH = str(dst)
            db = SP5Database(str(dst))
            result = db.get_employees()
            # Result must be a list, even if empty
            assert isinstance(result, list)
        finally:
            main_module.DB_PATH = old_db_path


# ── Long string input ─────────────────────────────────────────────────────────

class TestLongStringInput:
    """Verify very long string inputs don't cause server errors."""

    def test_employee_name_very_long_string(self, sync_client):
        """POST with very long name should return 4xx, not 500."""
        long_name = "A" * 10000
        resp = sync_client.post("/api/employees", json={
            "NAME": long_name,
            "SHORTNAME": "X",
        })
        # Should not be 500 (either created with truncation or rejected with 4xx)
        assert resp.status_code != 500

    def test_absence_note_very_long_string(self, sync_client):
        """Very long note in absence should not cause 500."""
        long_note = "B" * 10000
        resp = sync_client.post("/api/absences", json={
            "EMPID": 1,
            "STARTDATE": "2024-01-01",
            "ENDDATE": "2024-01-01",
            "REASON": "K",
            "NOTE": long_note,
        })
        assert resp.status_code != 500


# ── HTTP method enforcement ───────────────────────────────────────────────────

class TestHttpMethodEnforcement:
    """GET-only endpoints must reject DELETE/PUT/PATCH."""

    def test_schedule_rejects_delete(self, sync_client):
        resp = sync_client.delete("/api/schedule?year=2024&month=6")
        assert resp.status_code in (405, 422)

    def test_employees_list_rejects_put(self, sync_client):
        resp = sync_client.put("/api/employees")
        assert resp.status_code in (405, 422)

    def test_staffing_rejects_delete(self, sync_client):
        resp = sync_client.delete("/api/staffing?year=2024&month=6")
        assert resp.status_code in (405, 422)

    def test_schedule_conflicts_rejects_delete(self, sync_client):
        resp = sync_client.delete("/api/schedule/conflicts?year=2024&month=6")
        assert resp.status_code in (405, 422)


# ── Missing required parameters ───────────────────────────────────────────────

class TestMissingParameters:
    """Endpoints with required params should return 422 if missing."""

    def test_schedule_missing_year(self, sync_client):
        resp = sync_client.get("/api/schedule?month=6")
        assert resp.status_code == 422

    def test_schedule_missing_month(self, sync_client):
        resp = sync_client.get("/api/schedule?year=2024")
        assert resp.status_code == 422

    def test_staffing_missing_year(self, sync_client):
        resp = sync_client.get("/api/staffing?month=6")
        assert resp.status_code == 422

    def test_staffing_missing_month(self, sync_client):
        resp = sync_client.get("/api/staffing?year=2024")
        assert resp.status_code == 422

    def test_schedule_year_missing_employee_id(self, sync_client):
        resp = sync_client.get("/api/schedule/year?year=2024")
        assert resp.status_code == 422
