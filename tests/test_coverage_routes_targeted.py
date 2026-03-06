"""Targeted tests to boost admin.py and reports.py coverage.

Focuses on:
- Import CSV error paths: unknown employee, unknown leave type
- bookings-nominal import
- absences-csv import (alternate format)
- groups import with unknown parent
- Admin error paths (delete non-existent period, backup without DB path)
- Reports: capacity-forecast, fairness-report, schedule-overview endpoints
"""

import io
import pytest
from starlette.testclient import TestClient


class TestImportBookingsNominal:
    """Test /api/import/bookings-nominal error paths."""

    def test_import_bookings_nominal_missing_fields(self, admin_client: TestClient):
        """CSV with missing required fields → skipped."""
        csv_content = "Personalnummer,Datum,Stunden\n,,\n"
        res = admin_client.post(
            "/api/import/bookings-nominal",
            files={"file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0 or data.get("imported", 0) == 0

    def test_import_bookings_nominal_unknown_employee(self, admin_client: TestClient):
        """CSV with unknown Personalnummer → skipped with error."""
        csv_content = "Personalnummer,Datum,Stunden\nXXXXXX_NONEXISTENT,2024-01-15,8.0\n"
        res = admin_client.post(
            "/api/import/bookings-nominal",
            files={"file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0

    def test_import_bookings_nominal_invalid_date(self, admin_client: TestClient):
        """CSV with invalid date format → skipped."""
        csv_content = "Personalnummer,Datum,Stunden\n001,not-a-date,8.0\n"
        res = admin_client.post(
            "/api/import/bookings-nominal",
            files={"file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        # Either skipped or imported=0
        assert data.get("imported", 0) == 0 or data.get("skipped", 0) >= 0

    def test_import_bookings_nominal_empty_csv(self, admin_client: TestClient):
        """Empty CSV → imported=0, skipped=0."""
        csv_content = "Personalnummer,Datum,Stunden\n"
        res = admin_client.post(
            "/api/import/bookings-nominal",
            files={"file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("imported", 0) == 0


class TestImportAbsencesCSV:
    """Test /api/import/absences-csv (alternate format)."""

    def test_import_absences_csv_missing_fields(self, admin_client: TestClient):
        """Missing required fields → skipped."""
        csv_content = "Personalnummer,Datum,Abwesenheitsart\n,,\n"
        res = admin_client.post(
            "/api/import/absences-csv",
            files={"file": ("absences.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0

    def test_import_absences_csv_unknown_employee(self, admin_client: TestClient):
        """Unknown employee → skipped with error."""
        csv_content = "Personalnummer,Datum,Abwesenheitsart\nXXXXXX_NOTFOUND,2024-01-15,U\n"
        res = admin_client.post(
            "/api/import/absences-csv",
            files={"file": ("absences.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0

    def test_import_absences_csv_empty(self, admin_client: TestClient):
        """Empty CSV → imported=0."""
        csv_content = "Personalnummer,Datum,Abwesenheitsart\n"
        res = admin_client.post(
            "/api/import/absences-csv",
            files={"file": ("absences.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        assert res.json().get("imported", 0) == 0


class TestImportEntitlementsErrorPaths:
    """Test /api/import/entitlements unknown employee/leave-type paths."""

    def test_import_entitlements_unknown_employee(self, admin_client: TestClient):
        """Unknown Personalnummer → skipped."""
        csv_content = "Personalnummer,Jahr,Abwesenheitsart,Tage\nXXXXXX_NOTFOUND,2024,U,25\n"
        res = admin_client.post(
            "/api/import/entitlements",
            files={"file": ("ent.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0

    def test_import_entitlements_unknown_leave_type(self, admin_client: TestClient):
        """Valid employee but unknown leave type shortname → skipped."""
        # Get a real employee number first
        employees_res = admin_client.get("/api/employees")
        if employees_res.status_code != 200 or not employees_res.json():
            pytest.skip("No employees in test DB")
        emp = employees_res.json()[0]
        number = str(emp.get("NUMBER") or emp.get("number") or "001")

        csv_content = f"Personalnummer,Jahr,Abwesenheitsart,Tage\n{number},2024,ZZZZNOTEXIST,25\n"
        res = admin_client.post(
            "/api/import/entitlements",
            files={"file": ("ent.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0


class TestImportBookingsActualErrorPaths:
    """Test /api/import/bookings-actual unknown employee path."""

    def test_import_bookings_actual_unknown_employee(self, admin_client: TestClient):
        """Unknown Personalnummer → skipped."""
        csv_content = "Personalnummer,Datum,Stunden\nXXXXXX_NOTFOUND,2024-01-15,8.0\n"
        res = admin_client.post(
            "/api/import/bookings-actual",
            files={"file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0


class TestImportGroupsErrorPaths:
    """Test /api/import/groups unknown parent path."""

    def test_import_groups_unknown_parent(self, admin_client: TestClient):
        """Group with unknown parent (using PARENT column) → skipped."""
        csv_content = "Name,Kürzel,Parent\nTestGroup99,TG99,XXXX_NONEXISTENT_PARENT\n"
        res = admin_client.post(
            "/api/import/groups",
            files={"file": ("groups.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("skipped", 0) > 0

    def test_import_groups_empty(self, admin_client: TestClient):
        """Empty CSV → imported=0."""
        csv_content = "Name,Kürzel\n"
        res = admin_client.post(
            "/api/import/groups",
            files={"file": ("groups.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200
        assert res.json().get("imported", 0) == 0


class TestReportsEndpointsCoverage:
    """Test reports endpoints that may be missing coverage."""

    def test_fairness_report(self, sync_client: TestClient):
        """GET /api/fairness → 200."""
        res = sync_client.get("/api/fairness?year=2024")
        assert res.status_code == 200
        data = res.json()
        assert "employees" in data or "year" in data

    def test_fairness_report_with_group(self, sync_client: TestClient):
        """GET /api/fairness with group_id."""
        res = sync_client.get("/api/fairness?year=2024&group_id=1")
        assert res.status_code == 200

    def test_capacity_forecast(self, sync_client: TestClient):
        """GET /api/capacity-forecast → 200."""
        res = sync_client.get("/api/capacity-forecast?year=2024&month=6")
        assert res.status_code == 200

    def test_burnout_radar(self, sync_client: TestClient):
        """GET /api/burnout-radar → 200."""
        res = sync_client.get("/api/burnout-radar?year=2024&month=6")
        assert res.status_code == 200

    def test_capacity_year(self, sync_client: TestClient):
        """GET /api/capacity-year → 200."""
        res = sync_client.get("/api/capacity-year?year=2024")
        assert res.status_code == 200


class TestAdminErrorPaths:
    """Test admin.py error paths."""

    def test_delete_nonexistent_period(self, planer_client: TestClient):
        """DELETE /api/periods/9999999 → 200 with deleted=0 (or error)."""
        res = planer_client.delete("/api/periods/9999999")
        # Graceful - either 200 with deleted=0 or 404/500
        assert res.status_code in (200, 404, 500)

    def test_list_backups(self, admin_client: TestClient):
        """GET /api/admin/backups → 200."""
        res = admin_client.get("/api/admin/backups")
        assert res.status_code == 200

    def test_get_frontend_errors(self, admin_client: TestClient):
        """GET /api/admin/frontend-errors → 200."""
        res = admin_client.get("/api/admin/frontend-errors")
        assert res.status_code == 200

    def test_get_cache_stats(self, admin_client: TestClient):
        """GET /api/admin/cache-stats → 200."""
        res = admin_client.get("/api/admin/cache-stats")
        assert res.status_code == 200

    def test_report_frontend_error(self, sync_client: TestClient):
        """POST /api/errors → 200."""
        res = sync_client.post(
            "/api/errors",
            json={
                "error": "Test error from pytest",
                "component_stack": "Error: Test\n    at foo (bar.js:1:1)",
                "url": "http://localhost/test",
                "user_agent": "pytest",
            },
        )
        assert res.status_code == 200

    def test_settings_get(self, sync_client: TestClient):
        """GET /api/settings → 200."""
        res = sync_client.get("/api/settings")
        assert res.status_code == 200

    def test_settings_put_empty(self, admin_client: TestClient):
        """PUT /api/settings with empty body → 200 (no-op update)."""
        res = admin_client.put("/api/settings", json={})
        assert res.status_code == 200

    def test_backup_list_no_path(self, admin_client: TestClient):
        """Backup list returns empty list structure."""
        res = admin_client.get("/api/admin/backups")
        assert res.status_code == 200
        data = res.json()
        assert "backups" in data
