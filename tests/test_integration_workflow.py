"""
Integration test: vollständiger Workflow durch alle Rollen.

Testet einen realistischen Ablauf:
  1. Login als Planer (via Token-Injektion)
  2. Schedule-Eintrag erstellen
  3. Abwesenheit eintragen
  4. Als Leser einloggen → Lesezugriff prüfen
  5. Als Leser versuchen zu schreiben → 403
"""


class TestFullWorkflow:
    """Vollständiger Planer→Leser Workflow."""

    def test_planer_create_schedule_entry(self, planer_client):
        """Planer kann Schedule-Einträge anlegen."""
        emps = planer_client.get("/api/employees").json()
        shifts = planer_client.get("/api/shifts").json()
        assert emps, "Keine Mitarbeiter vorhanden"
        assert shifts, "Keine Schichten vorhanden"

        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]

        resp = planer_client.post("/api/schedule/bulk", json={
            "entries": [
                {"employee_id": emp_id, "date": "2025-07-01", "shift_id": shift_id},
            ],
            "overwrite": True
        })
        assert resp.status_code == 200, f"Schedule-Erstellung fehlgeschlagen: {resp.text}"
        data = resp.json()
        assert "created" in data

    def test_planer_create_absence(self, planer_client):
        """Planer kann Abwesenheiten eintragen."""
        emps = planer_client.get("/api/employees").json()
        assert emps, "Keine Mitarbeiter vorhanden"

        leave_types = planer_client.get("/api/leave-types").json()
        assert leave_types, "Keine Abwesenheitstypen vorhanden"

        emp_id = emps[0]["ID"]
        lt_id = leave_types[0]["ID"]

        resp = planer_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-07-10",
            "leave_type_id": lt_id,
        })
        assert resp.status_code == 200, f"Abwesenheit fehlgeschlagen: {resp.text}"
        assert resp.json().get("ok") is True

    def test_planer_create_note(self, planer_client):
        """Planer kann Notizen erstellen."""
        resp = planer_client.post("/api/notes", json={
            "date": "2025-07-15",
            "text": "Integration-Test Notiz",
        })
        assert resp.status_code == 200, f"Notiz erstellen fehlgeschlagen: {resp.text}"
        assert resp.json().get("ok") is True

    def test_leser_read_employees(self, leser_client):
        """Leser kann Mitarbeiter lesen."""
        resp = leser_client.get("/api/employees")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_leser_read_schedule(self, leser_client):
        """Leser kann Schedule lesen."""
        resp = leser_client.get("/api/schedule?year=2025&month=7")
        assert resp.status_code == 200

    def test_leser_read_shifts(self, leser_client):
        """Leser kann Schichten lesen."""
        resp = leser_client.get("/api/shifts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_leser_cannot_create_schedule(self, leser_client):
        """Leser darf KEINE Schedule-Einträge anlegen → 403."""
        emps = leser_client.get("/api/employees").json()
        shifts = leser_client.get("/api/shifts").json()
        emp_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]

        resp = leser_client.post("/api/schedule/bulk", json={
            "entries": [
                {"employee_id": emp_id, "date": "2025-07-02", "shift_id": shift_id},
            ],
            "overwrite": True
        })
        assert resp.status_code == 403, f"Leser sollte 403 bekommen, hat aber: {resp.status_code}"

    def test_leser_cannot_create_absence(self, leser_client):
        """Leser darf KEINE Abwesenheiten eintragen → 403."""
        emps = leser_client.get("/api/employees").json()
        leave_types = leser_client.get("/api/leave-types").json()
        emp_id = emps[0]["ID"]
        lt_id = leave_types[0]["ID"]

        resp = leser_client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": "2025-07-11",
            "leave_type_id": lt_id,
        })
        assert resp.status_code == 403, f"Leser sollte 403 bekommen, hat aber: {resp.status_code}"

    def test_leser_cannot_create_note(self, leser_client):
        """Leser darf KEINE Notizen erstellen → 403."""
        resp = leser_client.post("/api/notes", json={
            "date": "2025-07-16",
            "text": "Nicht erlaubt",
        })
        assert resp.status_code == 403, f"Leser sollte 403 bekommen, hat aber: {resp.status_code}"

    def test_leser_cannot_create_employee(self, leser_client):
        """Leser darf KEINE Mitarbeiter anlegen → 403."""
        resp = leser_client.post("/api/employees", json={
            "FIRSTNAME": "Max",
            "LASTNAME": "Mustermann",
        })
        assert resp.status_code == 403


class TestMasterDataCRUD:
    """CRUD-Tests für Master-Daten (Schichten, Gruppen)."""

    def test_get_shifts(self, planer_client):
        resp = planer_client.get("/api/shifts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_shifts_include_hidden(self, planer_client):
        resp = planer_client.get("/api/shifts?include_hidden=true")
        assert resp.status_code == 200

    def test_create_shift(self, write_client):
        resp = write_client.post("/api/shifts", json={
            "SHORTNAME": "IT",
            "NAME": "Integration Test Shift",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "record" in data

    def test_create_shift_missing_name(self, write_client):
        resp = write_client.post("/api/shifts", json={"SHORTNAME": "X"})
        assert resp.status_code == 422

    def test_get_groups(self, planer_client):
        resp = planer_client.get("/api/groups")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_leave_types(self, planer_client):
        resp = planer_client.get("/api/leave-types")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_workplaces(self, planer_client):
        resp = planer_client.get("/api/workplaces")
        assert resp.status_code == 200

    def test_get_holidays(self, planer_client):
        resp = planer_client.get("/api/holidays")
        assert resp.status_code == 200


class TestNotesCRUD:
    """CRUD-Tests für Notizen."""

    def test_create_and_read_note(self, write_client):
        # Create
        resp = write_client.post("/api/notes", json={
            "date": "2025-08-01",
            "text": "Notiz für CRUD-Test",
        })
        assert resp.status_code == 200
        created = resp.json()
        assert created.get("ok") is True
        created["record"]["id"]

        # Read
        resp2 = write_client.get("/api/notes?date=2025-08-01")
        assert resp2.status_code == 200

    def test_update_note(self, write_client):
        # Create
        resp = write_client.post("/api/notes", json={
            "date": "2025-08-02",
            "text": "Original",
        })
        assert resp.status_code == 200
        note_id = resp.json()["record"]["id"]

        # Update
        resp2 = write_client.put(f"/api/notes/{note_id}", json={"text": "Aktualisiert"})
        assert resp2.status_code == 200
        assert resp2.json().get("ok") is True

    def test_delete_note(self, write_client):
        # Create
        resp = write_client.post("/api/notes", json={
            "date": "2025-08-03",
            "text": "Zu löschen",
        })
        assert resp.status_code == 200
        note_id = resp.json()["record"]["id"]

        # Delete
        resp2 = write_client.delete(f"/api/notes/{note_id}")
        assert resp2.status_code == 200
        assert resp2.json().get("ok") is True

    def test_update_note_invalid_date(self, write_client):
        # Create
        resp = write_client.post("/api/notes", json={
            "date": "2025-08-04",
            "text": "Datum-Test",
        })
        assert resp.status_code == 200
        note_id = resp.json()["record"]["id"]

        # Update with invalid date
        resp2 = write_client.put(f"/api/notes/{note_id}", json={"date": "not-a-date"})
        assert resp2.status_code == 400
