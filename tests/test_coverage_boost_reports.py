"""Tests targeting uncovered lines in reports.py to boost coverage."""

import io

import pytest
from starlette.testclient import TestClient


class TestStatisticsEndpoints:
    """Tests for statistics endpoints."""

    def test_get_statistics_default(self, sync_client: TestClient):
        """GET /api/statistics → 200."""
        res = sync_client.get("/api/statistics")
        assert res.status_code == 200

    def test_get_statistics_with_params(self, sync_client: TestClient):
        """GET /api/statistics?year=2024&month=6 → 200."""
        res = sync_client.get("/api/statistics?year=2024&month=6")
        assert res.status_code == 200

    def test_get_statistics_invalid_month(self, sync_client: TestClient):
        """GET /api/statistics?month=13 → 400."""
        res = sync_client.get("/api/statistics?month=13")
        assert res.status_code == 400

    def test_get_year_summary(self, sync_client: TestClient):
        """GET /api/statistics/year-summary → 200."""
        res = sync_client.get("/api/statistics/year-summary")
        assert res.status_code == 200

    def test_get_year_summary_with_year(self, sync_client: TestClient):
        """GET /api/statistics/year-summary?year=2024 → 200."""
        res = sync_client.get("/api/statistics/year-summary?year=2024")
        assert res.status_code == 200

    def test_get_employee_statistics(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id} → 200."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        res = sync_client.get(f"/api/statistics/employee/{emp_id}")
        assert res.status_code == 200

    def test_get_employee_statistics_with_month(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id}?month=6 → 200."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        res = sync_client.get(f"/api/statistics/employee/{emp_id}?year=2024&month=6")
        assert res.status_code == 200

    def test_get_employee_statistics_not_found(self, sync_client: TestClient):
        """GET /api/statistics/employee/99999 → 404."""
        res = sync_client.get("/api/statistics/employee/99999")
        assert res.status_code == 404

    def test_get_employee_statistics_invalid_month(self, sync_client: TestClient):
        """GET /api/statistics/employee/{id}?month=13 → 400."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        res = sync_client.get(f"/api/statistics/employee/{emp_id}?month=13")
        assert res.status_code == 400

    def test_get_sickness_statistics(self, sync_client: TestClient):
        """GET /api/statistics/sickness → 200."""
        res = sync_client.get("/api/statistics/sickness")
        assert res.status_code == 200

    def test_get_shift_statistics(self, sync_client: TestClient):
        """GET /api/statistics/shifts?year=2024 → 200."""
        res = sync_client.get("/api/statistics/shifts?year=2024")
        assert res.status_code == 200

    def test_get_shift_statistics_with_group(self, sync_client: TestClient):
        """GET /api/statistics/shifts?year=2024&group_id=1 → 200."""
        res = sync_client.get("/api/statistics/shifts?year=2024&group_id=1")
        assert res.status_code == 200


class TestExportEndpoints:
    """Tests for export endpoints."""

    def test_export_schedule_csv(self, planer_client: TestClient):
        """GET /api/export/schedule → 200, CSV."""
        res = planer_client.get("/api/export/schedule?month=2024-06&format=csv")
        assert res.status_code == 200

    def test_export_schedule_xlsx(self, planer_client: TestClient):
        """GET /api/export/schedule format=xlsx → 200 or 500."""
        res = planer_client.get("/api/export/schedule?month=2024-06&format=xlsx")
        assert res.status_code in (200, 500)

    def test_export_schedule_csv_content(self, planer_client: TestClient):
        """Der CSV-Export trägt einen bekannten Dienst in die RICHTIGE Mitarbeiter-
        Zeile und die RICHTIGE Tagesspalte ein (Anzeigename == get_schedule) —
        test_export_schedule_csv prüfte nur status==200, nie den Inhalt eines
        Exports, der in die Lohnabrechnung fließt."""
        import csv as _csv
        import io

        emp = planer_client.get("/api/employees").json()[0]
        emp_id = emp["ID"]
        # Bekannter Dienst am 15. eines leeren Monats.
        r = planer_client.post(
            "/api/schedule",
            json={"employee_id": emp_id, "date": "2027-05-15", "shift_id": 1},
        )
        assert r.status_code == 200, r.text
        sched = planer_client.get("/api/schedule?year=2027&month=5").json()
        entry = next(
            e for e in sched
            if e["employee_id"] == emp_id and e["date"] == "2027-05-15"
        )
        expected = entry["display_name"]
        assert expected, "Testaufbau: Dienst hat keinen Anzeigenamen"

        exp = planer_client.get("/api/export/schedule?month=2027-05&format=csv")
        assert exp.status_code == 200
        assert exp.headers["content-type"].startswith("text/csv")
        rows = list(_csv.DictReader(io.StringIO(exp.text)))
        expected_name = f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", ")
        row = next((x for x in rows if x["Mitarbeiter"] == expected_name), None)
        assert row is not None, "Mitarbeiter-Zeile fehlt im CSV"
        # Dienst steht in Tagesspalte 15 mit dem Anzeigenamen, NICHT daneben.
        assert row["15"] == expected
        assert row["14"] == "" and row["16"] == ""

    def test_export_schedule_row_order_follows_default_sort(
        self, planer_client: TestClient, write_db_path
    ):
        """REGRESSION: der Export sortierte die Mitarbeiterzeilen nach POSITION
        um, während alle Ansichten die Original-Default-Ordnung (NAME,
        FIRSTNAME aus get_employees) nutzen — die Export-Zeilenfolge muss der
        get_employees-Folge entsprechen."""
        import csv as _csv
        import io

        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)
        employees = db.get_employees()
        assert len(employees) >= 2, "Testaufbau: mindestens 2 Mitarbeiter nötig"
        # POSITION absichtlich ENTGEGEN der Namensordnung setzen
        for pos, emp in enumerate(reversed(employees), start=1):
            db.update_employee(emp["ID"], {"POSITION": pos})

        exp = planer_client.get("/api/export/schedule?month=2027-06&format=csv")
        assert exp.status_code == 200
        rows = list(_csv.DictReader(io.StringIO(exp.text)))
        expected = [
            f"{e.get('NAME', '')}, {e.get('FIRSTNAME', '')}".strip(", ")
            for e in db.get_employees()
        ]
        assert [r["Mitarbeiter"] for r in rows] == expected

    def test_export_schedule_bad_month(self, planer_client: TestClient):
        """GET /api/export/schedule with bad month → 400."""
        res = planer_client.get("/api/export/schedule?month=2024-13&format=csv")
        assert res.status_code == 400

    def test_export_employees_content(self, planer_client: TestClient):
        """CSV-Mitarbeiterliste enthält je MA die echten Stammdaten (Name/Vorname/
        Kürzel) in den richtigen Spalten — test_export_employees prüfte nur 200."""
        import csv as _csv
        import io

        emp = planer_client.get("/api/employees").json()[0]
        res = planer_client.get("/api/export/employees?format=csv")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        rows = list(_csv.DictReader(io.StringIO(res.text)))
        row = next((r for r in rows if str(r["ID"]) == str(emp["ID"])), None)
        assert row is not None, "Mitarbeiter fehlt in der CSV-Liste"
        # Name und Vorname getrennt (kein Spalten-Swap), Kürzel korrekt.
        assert row["Name"] == emp["NAME"]
        assert row["Vorname"] == emp["FIRSTNAME"]
        assert row["Kürzel"] == emp["SHORTNAME"]

    def test_export_absences_content(self, planer_client: TestClient):
        """CSV-Abwesenheitsliste enthält einen bekannten Urlaub mit MA + Art +
        Datum (ABSEN↔MA↔Abwesenheitsart-Join) — test_export_absences prüfte nur 200."""
        import csv as _csv
        import io

        emp = planer_client.get("/api/employees").json()[0]
        lts = planer_client.get("/api/leave-types").json()
        lt = (lts if isinstance(lts, list) else lts.get("leave_types"))[0]
        r = planer_client.post("/api/absences", json={
            "employee_id": emp["ID"], "date": "2028-04-10",
            "leave_type_id": lt["ID"], "interval": 0})
        assert r.status_code == 200, r.text

        res = planer_client.get("/api/export/absences?year=2028&format=csv")
        assert res.status_code == 200
        rows = list(_csv.DictReader(io.StringIO(res.text)))
        expected_name = f"{emp['NAME']}, {emp['FIRSTNAME']}".strip(", ")
        row = next((x for x in rows if x["Datum"] == "2028-04-10"
                    and x["Mitarbeiter"] == expected_name), None)
        assert row is not None, "Abwesenheit fehlt in der CSV-Liste"
        assert row["Abwesenheitsart"] == lt["NAME"]
        assert row["Kürzel Art"] == lt["SHORTNAME"]

    def test_export_statistics(self, sync_client: TestClient):
        """GET /api/export/statistics → 200."""
        res = sync_client.get("/api/export/statistics?year=2024&month=6")
        assert res.status_code == 200

    def test_export_statistics_content(self, planer_client: TestClient):
        """Der Statistik-Export trägt die ECHTEN Lohn-Zahlen (Soll/Ist/Überstunden
        je MA und Monat) — identisch zu GET /api/statistics. Bisher prüften alle
        Tests nur Status/Format/XLSX-Struktur, nie die Werte, die in die Lohn-
        abrechnung fließen."""
        import csv as _csv
        import io

        emp = planer_client.get("/api/employees").json()[0]
        emp_id = emp["ID"]
        # Zwei Ist-Frühschichten (je 8 h) in einem leeren Monat → actual_hours == 16.
        for d in ("2029-06-05", "2029-06-06"):
            r = planer_client.post(
                "/api/schedule",
                json={"employee_id": emp_id, "date": d, "shift_id": 1},
            )
            assert r.status_code == 200, r.text

        stats = planer_client.get("/api/statistics?year=2029&month=6").json()
        ref = next(s for s in stats if s["employee_id"] == emp_id)
        assert ref["actual_hours"] == 16.0, ref  # 2×8 h flossen wirklich ein

        res = planer_client.get("/api/export/statistics?year=2029&format=csv")
        assert res.status_code == 200
        rows = list(_csv.DictReader(io.StringIO(res.text)))
        row = next(
            (x for x in rows
             if x["Monat"] == "6" and x["Kürzel"] == emp.get("SHORTNAME", "")),
            None,
        )
        assert row is not None, "MA-Monatszeile fehlt im Statistik-Export"
        # Export rendert exakt die get_statistics-Werte (keine Rundungs-/Spalten-Bugs).
        assert float(row["Ist (h)"]) == ref["actual_hours"]
        assert float(row["Soll (h)"]) == ref["target_hours"]
        assert float(row["Überstunden (h)"]) == ref["overtime_hours"]

    def test_export_employees(self, sync_client: TestClient):
        """GET /api/export/employees → 200."""
        res = sync_client.get("/api/export/employees")
        assert res.status_code == 200

    def test_export_absences(self, sync_client: TestClient):
        """GET /api/export/absences → 200."""
        res = sync_client.get("/api/export/absences?year=2024")
        assert res.status_code == 200


class TestBookings:
    """Tests for booking CRUD."""

    def test_get_bookings(self, sync_client: TestClient):
        """GET /api/bookings → 200."""
        res = sync_client.get("/api/bookings")
        assert res.status_code == 200

    def test_create_booking_invalid_date(self, planer_client: TestClient):
        """POST /api/bookings with valid format but invalid date → 400."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings",
            json={
                "employee_id": emps[0]["ID"],
                "date": "2024-13-01",  # valid format, invalid date → function raises 400
                "type": 0,
                "value": 8.0,
            },
        )
        assert res.status_code == 400

    def test_create_booking_invalid_type(self, planer_client: TestClient):
        """POST /api/bookings with type=99 → 422 (Pydantic, range 0-1)."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings",
            json={
                "employee_id": emps[0]["ID"],
                "date": "2024-06-01",
                "type": 99,
                "value": 8.0,
            },
        )
        assert res.status_code == 422

    def test_create_and_delete_booking(self, planer_client: TestClient):
        """POST then DELETE booking → 200."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings",
            json={
                "employee_id": emps[0]["ID"],
                "date": "2024-06-01",
                "type": 0,
                "value": 8.0,
                "note": "test",
            },
        )
        assert res.status_code == 200
        booking_id = res.json()["record"]["id"]
        del_res = planer_client.delete(f"/api/bookings/{booking_id}")
        assert del_res.status_code == 200

    def test_update_booking_roundtrip(self, planer_client: TestClient):
        """POST then PUT booking → geänderte Felder werden gespeichert (404 sonst)."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings",
            json={"employee_id": emps[0]["ID"], "date": "2024-06-02", "type": 0,
                  "value": 8.0, "note": "alt"},
        )
        assert res.status_code == 200
        booking_id = res.json()["record"]["id"]
        upd = planer_client.put(
            f"/api/bookings/{booking_id}", json={"value": 5.5, "note": "neu"}
        )
        assert upd.status_code == 200, upd.text
        rec = upd.json()["record"]
        assert abs(rec["value"] - 5.5) < 1e-6
        assert rec["note"] == "neu"
        planer_client.delete(f"/api/bookings/{booking_id}")

    def test_update_booking_not_found(self, planer_client: TestClient):
        """PUT /api/bookings/99999 → 404."""
        res = planer_client.put("/api/bookings/99999", json={"value": 1.0})
        assert res.status_code == 404

    def test_delete_booking_not_found(self, planer_client: TestClient):
        """DELETE /api/bookings/99999 → 404."""
        res = planer_client.delete("/api/bookings/99999")
        assert res.status_code == 404


class TestCarryForward:
    """Tests for carry-forward operations."""

    def test_get_carry_forward(self, sync_client: TestClient):
        """GET /api/bookings/carry-forward → 200."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(
            f"/api/bookings/carry-forward?employee_id={emps[0]['ID']}&year=2024"
        )
        assert res.status_code == 200

    def test_set_carry_forward(self, planer_client: TestClient):
        """POST /api/bookings/carry-forward → 200."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings/carry-forward",
            json={
                "employee_id": emps[0]["ID"],
                "year": 2024,
                "hours": 10.5,
            },
        )
        assert res.status_code == 200

    def test_annual_statement(self, planer_client: TestClient):
        """POST /api/bookings/annual-statement → 200."""
        emps = planer_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = planer_client.post(
            "/api/bookings/annual-statement",
            json={
                "employee_id": emps[0]["ID"],
                "year": 2024,
            },
        )
        assert res.status_code == 200

    def test_get_overtime_records(self, sync_client: TestClient):
        """GET /api/overtime-records → 200."""
        res = sync_client.get("/api/overtime-records")
        assert res.status_code == 200


class TestMonthlyReport:
    """Tests for monthly report."""

    def test_get_monthly_report(self, sync_client: TestClient):
        """GET /api/reports/monthly → 200."""
        res = sync_client.get("/api/reports/monthly?year=2024&month=6")
        assert res.status_code == 200

    def test_get_monthly_report_all_employees(self, sync_client: TestClient):
        """GET /api/reports/monthly all employees → 200."""
        res = sync_client.get("/api/reports/monthly?year=2024&month=6")
        assert res.status_code == 200


class TestZeitkonto:
    """Tests for Zeitkonto endpoints."""

    def test_get_zeitkonto(self, sync_client: TestClient):
        """GET /api/zeitkonto → 200."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(f"/api/zeitkonto?employee_id={emps[0]['ID']}&year=2024")
        assert res.status_code == 200

    def test_get_zeitkonto_summary(self, sync_client: TestClient):
        """GET /api/zeitkonto/summary → 200."""
        res = sync_client.get("/api/zeitkonto/summary?year=2024")
        assert res.status_code == 200

    def test_get_zeitkonto_detail(self, sync_client: TestClient):
        """GET /api/zeitkonto/detail → 200."""
        emps = sync_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        res = sync_client.get(
            f"/api/zeitkonto/detail?employee_id={emps[0]['ID']}&year=2024&month=6"
        )
        assert res.status_code == 200


class TestImportEndpoints:
    """Tests for CSV import endpoints (mainly error paths)."""

    def _make_csv(self, header: str, rows: list[str]) -> bytes:
        """Build CSV bytes."""
        content = "\n".join([header] + rows)
        return content.encode("utf-8")

    def test_import_employees_invalid_content_type(self, admin_client: TestClient):
        """POST /api/import/employees with non-CSV → 400."""
        res = admin_client.post(
            "/api/import/employees",
            files={"file": ("data.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert res.status_code == 400

    def test_import_employees_too_large(self, admin_client: TestClient):
        """POST /api/import/employees with file > 10MB → 413."""
        big = b"NAME,FIRSTNAME\n" + b"a" * (11 * 1024 * 1024)
        res = admin_client.post(
            "/api/import/employees",
            files={"file": ("data.csv", io.BytesIO(big), "text/csv")},
        )
        assert res.status_code == 413

    def test_import_employees_valid_csv(self, admin_client: TestClient):
        """POST /api/import/employees with valid CSV → 200."""
        csv_content = "NAME,FIRSTNAME,SHORTNAME\nImportTest,Hans,IT1\n"
        res = admin_client.post(
            "/api/import/employees",
            files={
                "file": ("employees.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_shifts_valid_csv(self, admin_client: TestClient):
        """POST /api/import/shifts → 200."""
        csv_content = "NAME,SHORTNAME,FROM0,TO0\nFrühschicht,F,06:00,14:00\n"
        res = admin_client.post(
            "/api/import/shifts",
            files={
                "file": ("shifts.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_absences_valid_csv(self, admin_client: TestClient):
        """POST /api/import/absences → 200."""
        emps = admin_client.get("/api/employees").json()
        leave_types = admin_client.get("/api/leave-types").json()
        if not emps or not leave_types:
            pytest.skip("No data")
        emp_id = emps[0]["ID"]
        lt_id = leave_types[0]["ID"]
        csv_content = f"employee_id,date,leave_type_id\n{emp_id},2025-11-01,{lt_id}\n"
        res = admin_client.post(
            "/api/import/absences",
            files={
                "file": ("absences.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_holidays_valid_csv(self, admin_client: TestClient):
        """POST /api/import/holidays → 200."""
        csv_content = "date,name\n2024-01-01,Neujahr\n"
        res = admin_client.post(
            "/api/import/holidays",
            files={
                "file": ("holidays.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_groups_valid_csv(self, admin_client: TestClient):
        """POST /api/import/groups → 200."""
        csv_content = "NAME,SHORTNAME\nImportGrp,IG\n"
        res = admin_client.post(
            "/api/import/groups",
            files={
                "file": ("groups.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_bookings_actual(self, admin_client: TestClient):
        """POST /api/import/bookings-actual → 200."""
        emps = admin_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"employee_id,date,hours\n{emp_id},2024-06-01,8.0\n"
        res = admin_client.post(
            "/api/import/bookings-actual",
            files={
                "file": ("bookings.csv", io.BytesIO(csv_content.encode()), "text/csv")
            },
        )
        assert res.status_code == 200

    def test_import_entitlements_valid_csv(self, admin_client: TestClient):
        """POST /api/import/entitlements → 200."""
        emps = admin_client.get("/api/employees").json()
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        csv_content = f"employee_id,year,days\n{emp_id},2024,25\n"
        res = admin_client.post(
            "/api/import/entitlements",
            files={"file": ("ent.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        )
        assert res.status_code == 200


class TestAnalyticsEndpoints:
    """Tests for analytics/reporting endpoints."""

    def test_get_burnout_radar(self, sync_client: TestClient):
        """GET /api/burnout-radar → 200."""
        res = sync_client.get("/api/burnout-radar?year=2024&month=6")
        assert res.status_code == 200

    def test_get_overtime_summary(self, sync_client: TestClient):
        """GET /api/overtime-summary → 200."""
        res = sync_client.get("/api/overtime-summary?year=2024")
        assert res.status_code == 200

    def test_get_warnings(self, sync_client: TestClient):
        """GET /api/warnings → 200."""
        res = sync_client.get("/api/warnings")
        assert res.status_code == 200

    def test_get_fairness_score(self, sync_client: TestClient):
        """GET /api/fairness → 200."""
        res = sync_client.get("/api/fairness?year=2024")
        assert res.status_code == 200

    def test_get_capacity_forecast(self, sync_client: TestClient):
        """GET /api/capacity-forecast → 200."""
        res = sync_client.get("/api/capacity-forecast?year=2024&month=6")
        assert res.status_code == 200

    def test_get_capacity_year(self, sync_client: TestClient):
        """GET /api/capacity-year → 200."""
        res = sync_client.get("/api/capacity-year?year=2024")
        assert res.status_code == 200

    def test_get_quality_report(self, sync_client: TestClient):
        """GET /api/quality-report → 200."""
        res = sync_client.get("/api/quality-report?year=2024&month=6")
        assert res.status_code == 200

    def test_get_availability_matrix(self, sync_client: TestClient):
        """GET /api/availability-matrix → 200."""
        res = sync_client.get("/api/availability-matrix")
        assert res.status_code == 200

    def test_run_simulation(self, sync_client: TestClient):
        """POST /api/simulation → 200."""
        res = sync_client.post(
            "/api/simulation", json={"year": 2024, "month": 6, "absences": []}
        )
        assert res.status_code == 200
