"""Tests for Q064: CSV employee import endpoint."""

import io


def _make_csv(rows: list[str], delimiter: str = ",") -> bytes:
    """Build a CSV file from a list of raw lines."""
    return "\n".join(rows).encode("utf-8")


class TestCSVImportEndpoint:
    """POST /api/v1/employees/import-csv"""

    # ── Happy path ────────────────────────────────────────────

    def test_import_basic(self, write_client):
        """Import a simple CSV with 2 employees."""
        csv_data = _make_csv([
            "first_name,last_name,email",
            "Max,Importtest,max@example.com",
            "Anna,Importtest,anna@example.com",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("employees.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == []

    def test_import_with_all_columns(self, write_client):
        """Import CSV with all optional columns."""
        csv_data = _make_csv([
            "first_name,last_name,email,phone,contract_hours,qualifications",
            "Lisa,Volltest,lisa@test.at,+43123456,38.5,Ersthelfer",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("full.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1

    def test_import_semicolon_delimiter(self, write_client):
        """Import CSV using semicolon delimiter (common in German locale)."""
        csv_data = _make_csv([
            "first_name;last_name;email",
            "Fritz;Semikolon;fritz@test.at",
        ], delimiter=";")
        # _make_csv just joins — the actual delimiter is in the content
        csv_data = b"first_name;last_name;email\nFritz;Semikolon;fritz@test.at\n"
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("semi.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1

    def test_import_with_bom(self, write_client):
        """Import CSV with UTF-8 BOM (Excel export)."""
        csv_data = b"\xef\xbb\xbffirst_name,last_name\nBOM,Testperson\n"
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("bom.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    # ── Duplicate handling ────────────────────────────────────

    def test_import_skips_duplicates(self, write_client):
        """Second import of same names should skip them."""
        csv_data = _make_csv([
            "first_name,last_name",
            "Duplikat,Testfall",
        ])
        # First import
        resp1 = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("d1.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp1.json()["created"] == 1

        # Second import — same name
        resp2 = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("d2.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data2 = resp2.json()
        assert data2["created"] == 0
        assert data2["skipped"] == 1

    def test_import_skips_duplicates_within_csv(self, write_client):
        """Duplicate names within the same CSV should be created only once."""
        csv_data = _make_csv([
            "first_name,last_name",
            "InternDup,Person",
            "InternDup,Person",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("dup.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 1
        assert data["skipped"] == 1

    # ── Validation errors ─────────────────────────────────────

    def test_import_missing_required_fields(self, write_client):
        """Rows missing first_name or last_name should produce errors."""
        csv_data = _make_csv([
            "first_name,last_name,email",
            ",Nachname,a@b.com",
            "Vorname,,b@c.com",
            "Valid,Person1,c@d.com",
            "Also,Valid2,e@f.com",
            "Third,Valid3,g@h.com",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("miss.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 3
        assert len(data["errors"]) >= 2
        # Check error structure
        for err in data["errors"]:
            assert "row" in err
            assert "field" in err
            assert "message" in err

    def test_import_invalid_email(self, write_client):
        """Invalid email format should produce an error."""
        csv_data = _make_csv([
            "first_name,last_name,email",
            "Bad,Email,not-an-email",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("bad_email.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["field"] == "email"

    def test_import_invalid_group_id(self, write_client):
        """Non-existent group_id should produce an error."""
        csv_data = _make_csv([
            "first_name,last_name,group_id",
            "Test,GroupFail,999999",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("grp.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert any(e["field"] == "group_id" for e in data["errors"])

    def test_import_invalid_group_id_non_numeric(self, write_client):
        """Non-numeric group_id should produce an error."""
        csv_data = _make_csv([
            "first_name,last_name,group_id",
            "Test,GroupText,abc",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("grp2.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert any(e["field"] == "group_id" for e in data["errors"])

    def test_import_invalid_contract_hours(self, write_client):
        """Invalid contract_hours should produce an error."""
        csv_data = _make_csv([
            "first_name,last_name,contract_hours",
            "Test,HoursFail,abc",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("hrs.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert any(e["field"] == "contract_hours" for e in data["errors"])

    def test_import_contract_hours_out_of_range(self, write_client):
        """contract_hours > 168 should produce an error."""
        csv_data = _make_csv([
            "first_name,last_name,contract_hours",
            "Test,HoursRange,200",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("hrs2.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert any(e["field"] == "contract_hours" for e in data["errors"])

    # ── Missing header columns ────────────────────────────────

    def test_import_missing_header(self, write_client):
        """CSV without required header columns should fail."""
        csv_data = _make_csv([
            "email,phone",
            "a@b.com,123",
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("noheader.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 400
        assert "Pflicht-Spalten" in resp.json()["detail"]

    # ── Empty file ────────────────────────────────────────────

    def test_import_empty_file(self, write_client):
        """Empty CSV should return 400."""
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        )
        assert resp.status_code == 400

    def test_import_header_only(self, write_client):
        """CSV with header but no data rows should return 400."""
        csv_data = _make_csv(["first_name,last_name"])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("hdr.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 400

    # ── Error threshold / rollback ────────────────────────────

    def test_import_rollback_on_too_many_errors(self, write_client):
        """If >50% of rows have errors, all should be rolled back."""
        csv_data = _make_csv([
            "first_name,last_name,email",
            ",Bad1,a@b.com",       # error: no first_name
            ",Bad2,c@d.com",       # error: no first_name
            ",Bad3,e@f.com",       # error: no first_name
            "Good,Person,g@h.com", # valid
        ])
        resp = write_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("rollback.csv", io.BytesIO(csv_data), "text/csv")},
        )
        data = resp.json()
        assert data["created"] == 0
        assert data.get("rolled_back") is True

    # ── Permission checks ─────────────────────────────────────

    def test_import_requires_admin(self, planer_client):
        """Non-admin users should get 403."""
        csv_data = _make_csv([
            "first_name,last_name",
            "Nope,Forbidden",
        ])
        resp = planer_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("perm.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 403

    def test_import_requires_auth(self, write_db_path, app):
        """Unauthenticated requests should get 401."""
        from starlette.testclient import TestClient

        with TestClient(app) as c:
            csv_data = _make_csv(["first_name,last_name", "No,Auth"])
            resp = c.post(
                "/api/v1/employees/import-csv",
                files={"file": ("noauth.csv", io.BytesIO(csv_data), "text/csv")},
            )
            assert resp.status_code == 401

    def test_import_leser_forbidden(self, leser_client):
        """Leser role should get 403."""
        csv_data = _make_csv(["first_name,last_name", "No,Leser"])
        resp = leser_client.post(
            "/api/v1/employees/import-csv",
            files={"file": ("leser.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 403

    # ── Versioned endpoint ────────────────────────────────────

    def test_import_via_unversioned_path(self, write_client):
        """The unversioned /api/ path should also work (with deprecation headers)."""
        csv_data = _make_csv([
            "first_name,last_name",
            "Unversioned,Path",
        ])
        resp = write_client.post(
            "/api/employees/import-csv",
            files={"file": ("unv.csv", io.BytesIO(csv_data), "text/csv")},
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 1
