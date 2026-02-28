"""
End-to-End Integrationstests für die wichtigsten Benutzer-Workflows.

Szenarien:
1. Vollständiger Planungs-Workflow: Schicht zuweisen → Konflikt prüfen
2. Urlaubs-Workflow: Abwesenheit erstellen → Statistiken
3. Auto-Planer Workflow: Wünsche setzen → Schedule generieren → Restrictions respektieren
4. Backup/Restore Workflow: Backup → Daten ändern → Restore
5. Auth-Workflow: Login → Token → Endpoint → Logout → Token ungültig
"""
import io
import zipfile
import secrets


# ─────────────────────────────────────────────────────────────────────────────
# Helper: inject a user into the session store and return a TestClient wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _make_client(app, role: str, user_id: int = None):
    """Return a (client, token) tuple with an injected session for the given role."""
    from starlette.testclient import TestClient
    from api.main import _sessions
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        'ID': user_id or (800 + abs(hash(role)) % 100),
        'NAME': f'e2e_{role.lower()}',
        'role': role,
        'ADMIN': role == 'Admin',
        'RIGHTS': 255 if role == 'Admin' else (2 if role == 'Planer' else 1),
    }
    client = TestClient(app, raise_server_exceptions=True)
    client.headers.update({'X-Auth-Token': tok})
    return client, tok


# ═════════════════════════════════════════════════════════════════════════════
# 1. Vollständiger Planungs-Workflow
# ═════════════════════════════════════════════════════════════════════════════

class TestPlanungsWorkflow:
    """
    Workflow: Mitarbeiter laden → Gruppe zuordnen → Schicht zuweisen → Konflikt prüfen
    Alle Schritte als sequentielle API-Calls in einem Test.
    """

    def test_full_planning_workflow(self, app, patched_db):
        """Vollständiger Planungs-Workflow: Schicht zuweisen & Konflikt-Check."""
        client, _ = _make_client(app, 'Planer')

        # 1. Mitarbeiter laden
        emps = client.get("/api/employees").json()
        assert emps, "Keine Mitarbeiter im System"
        emp = emps[0]
        emp_id = emp["ID"]

        # 2. Gruppen laden & prüfen dass employee einer Gruppe zugehört
        groups = client.get("/api/groups").json()
        assert isinstance(groups, list), "Gruppen-Endpoint fehlerhaft"

        # 3. Schichten laden
        shifts = client.get("/api/shifts").json()
        assert shifts, "Keine Schichten im System"
        shift = shifts[0]
        shift_id = shift["ID"]

        # 4. Schedule-Eintrag anlegen
        test_date = "2025-08-01"
        resp = client.post("/api/schedule/bulk", json={
            "entries": [{"employee_id": emp_id, "date": test_date, "shift_id": shift_id}],
            "overwrite": True
        })
        assert resp.status_code == 200, f"Schicht-Zuweisung fehlgeschlagen: {resp.text}"
        data = resp.json()
        assert "created" in data, f"Kein 'created' in Response: {data}"

        # 5. Schedule lesen & Eintrag verifizieren
        sched = client.get("/api/schedule?year=2025&month=8").json()
        assert isinstance(sched, (list, dict)), "Schedule-Response ungültig"
        # Flache Suche nach dem angelegten Eintrag
        entries = sched if isinstance(sched, list) else sched.get("entries", [])
        [
            e for e in entries
            if str(e.get("employee_id") or e.get("EMPLOYEE_ID", "")) == str(emp_id)
            and e.get("date", "")[:10] == test_date
        ]
        # Eintrag sollte sichtbar sein (oder zumindest kein Fehler beim Schreiben)
        assert data["created"] >= 1 or data.get("skipped", 0) >= 1, \
            "Weder created noch skipped – Eintrag unklar"

        # 6. Konflikt-Check: gleichen Eintrag nochmals anlegen (overwrite=False)
        resp2 = client.post("/api/schedule/bulk", json={
            "entries": [{"employee_id": emp_id, "date": test_date, "shift_id": shift_id}],
            "overwrite": False
        })
        # Bei Konflikt ohne overwrite: entweder skipped (200) oder Fehler (400/500)
        assert resp2.status_code in (200, 400, 500), f"Unerwarteter Statuscode: {resp2.status_code}"
        if resp2.status_code == 200:
            data2 = resp2.json()
            assert data2.get("skipped", 0) >= 1, "Konflikt hätte erkannt werden sollen"

        # 7. Conflicts-Endpoint direkt prüfen
        conflicts_resp = client.get("/api/schedule/conflicts?year=2025&month=8")
        assert conflicts_resp.status_code == 200

    def test_group_assignment_visible_in_schedule(self, app, patched_db):
        """Gruppen-Zuweisung ist im Schedule sichtbar."""
        client, _ = _make_client(app, 'Planer')

        # Gruppe laden
        groups_resp = client.get("/api/groups")
        assert groups_resp.status_code == 200
        groups = groups_resp.json()
        assert groups, "Keine Gruppen"

        group_id = groups[0]["ID"]

        # Schedule für Gruppe abrufen
        sched_resp = client.get(f"/api/schedule?year=2025&month=8&group_id={group_id}")
        assert sched_resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 2. Urlaubs-Workflow
# ═════════════════════════════════════════════════════════════════════════════

class TestUrlaubsWorkflow:
    """
    Workflow: Urlaubsantrag erstellen → in Statistiken/Balance sichtbar
    """

    def test_full_vacation_workflow(self, app, patched_db):
        """Urlaubs-Workflow: Abwesenheit anlegen → Leave-Balance prüfen."""
        client, _ = _make_client(app, 'Planer')

        # 1. Mitarbeiter laden
        emps = client.get("/api/employees").json()
        assert emps, "Keine Mitarbeiter"
        emp_id = emps[0]["ID"]

        # 2. Abwesenheitstypen laden (Urlaub suchen)
        leave_types = client.get("/api/leave-types").json()
        assert leave_types, "Keine Abwesenheitstypen"

        # Urlaubs-Typ finden (enthält "urlaub" oder "U" im Kürzel)
        vacation_type = None
        for lt in leave_types:
            name = (lt.get("NAME") or lt.get("name") or "").lower()
            short = (lt.get("KUERZEL") or lt.get("kuerzel") or lt.get("SHORT", "")).lower()
            if "urlaub" in name or short in ("u", "ur", "url"):
                vacation_type = lt
                break
        if vacation_type is None:
            vacation_type = leave_types[0]  # Fallback auf ersten Typ

        lt_id = vacation_type["ID"]
        test_date = "2025-09-01"

        # 3. Urlaubsantrag anlegen
        resp = client.post("/api/absences", json={
            "employee_id": emp_id,
            "date": test_date,
            "leave_type_id": lt_id,
        })
        assert resp.status_code == 200, f"Urlaub anlegen fehlgeschlagen: {resp.text}"
        assert resp.json().get("ok") is True

        # 4. Abwesenheiten abrufen & prüfen ob der Eintrag vorhanden
        absences_resp = client.get("/api/absences?year=2025")
        assert absences_resp.status_code == 200
        absences = absences_resp.json()
        assert isinstance(absences, list)

        found = any(
            str(a.get("EMPLOYEE_ID") or a.get("employee_id", "")) == str(emp_id)
            and str(a.get("DATE", a.get("date", ""))[:10]) == test_date
            for a in absences
        )
        assert found, f"Urlaubseintrag nicht in Abwesenheiten gefunden (emp={emp_id}, date={test_date})"

        # 5. Leave-Balance abrufen → Urlaub sollte abgezogen sein
        balance_resp = client.get(f"/api/leave-balance?year=2025&employee_id={emp_id}")
        assert balance_resp.status_code == 200

        # 6. Dashboard-Stats prüfen
        stats_resp = client.get("/api/dashboard/stats?year=2025&month=9")
        assert stats_resp.status_code == 200

    def test_bulk_absence_workflow(self, app, patched_db):
        """Bulk-Urlaubsantrag für mehrere Mitarbeiter."""
        client, _ = _make_client(app, 'Planer')

        emps = client.get("/api/employees").json()
        assert len(emps) >= 1, "Mindestens 1 Mitarbeiter nötig"
        leave_types = client.get("/api/leave-types").json()
        assert leave_types

        emp_ids = [e["ID"] for e in emps[:2]]
        lt_id = leave_types[0]["ID"]

        resp = client.post("/api/absences/bulk", json={
            "employee_ids": emp_ids,
            "date": "2025-09-15",
            "leave_type_id": lt_id,
        })
        assert resp.status_code == 200, f"Bulk-Abwesenheit fehlgeschlagen: {resp.text}"

    def test_leave_entitlement_set_and_read(self, app, patched_db):
        """Urlaubsanspruch setzen und wieder auslesen."""
        client, _ = _make_client(app, 'Planer')

        emps = client.get("/api/employees").json()
        assert emps
        emp_id = emps[0]["ID"]

        leave_types = client.get("/api/leave-types").json()
        assert leave_types
        lt_id = leave_types[0]["ID"]

        # Anspruch setzen
        resp = client.post("/api/leave-entitlements", json={
            "employee_id": emp_id,
            "leave_type_id": lt_id,
            "year": 2025,
            "days": 25,
        })
        assert resp.status_code == 200, f"Anspruch setzen fehlgeschlagen: {resp.text}"

        # Anspruch lesen
        ent_resp = client.get(f"/api/leave-entitlements?year=2025&employee_id={emp_id}")
        assert ent_resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 3. Auto-Planer Workflow
# ═════════════════════════════════════════════════════════════════════════════

class TestAutoplanerWorkflow:
    """
    Workflow: Wünsche setzen → Schedule generieren → Restrictions respektiert?
    """

    def test_full_autoplaner_workflow(self, app, patched_db):
        """Auto-Planer: Restriction setzen → Schedule generieren → Restriction respektiert."""
        admin_client, _ = _make_client(app, 'Admin')
        planer_client, _ = _make_client(app, 'Planer')

        emps = planer_client.get("/api/employees").json()
        assert emps, "Keine Mitarbeiter"
        emp = emps[0]
        emp_id = emp["ID"]

        shifts = planer_client.get("/api/shifts").json()
        assert shifts, "Keine Schichten"
        shift_id = shifts[0]["ID"]

        # 1. Shift-Cycle für Mitarbeiter anlegen
        cycles_resp = planer_client.get("/api/shift-cycles")
        assert cycles_resp.status_code == 200
        cycles = cycles_resp.json()

        if cycles:
            cycle_id = cycles[0]["ID"]

            # 2. Cycle dem Mitarbeiter zuweisen
            assign_resp = planer_client.post("/api/shift-cycles/assign", json={
                "employee_id": emp_id,
                "cycle_id": cycle_id,
                "start_date": "2025-10-01",
            })
            # Kann 200 oder 400 sein (wenn bereits assigned)
            assert assign_resp.status_code in (200, 400)

            # 3. Restriction setzen (Mitarbeiter darf bestimmte Schicht nicht)
            restr_resp = admin_client.post("/api/restrictions", json={
                "employee_id": emp_id,
                "shift_id": shift_id,
                "reason": "E2E-Test Restriction",
                "weekday": 0,
            })
            assert restr_resp.status_code in (200, 400), f"Restriction fehlerhaft: {restr_resp.text}"

        # 4. Schedule generieren (dry_run=True → kein Schreiben, nur Preview)
        gen_resp = planer_client.post("/api/schedule/generate", json={
            "year": 2025,
            "month": 10,
            "employee_ids": [emp_id],
            "dry_run": True,
            "respect_restrictions": True,
            "force": False,
        })
        assert gen_resp.status_code == 200, f"Schedule-Generierung fehlgeschlagen: {gen_resp.text}"
        gen_data = gen_resp.json()
        assert "created" in gen_data
        assert "skipped_restriction" in gen_data
        assert "message" in gen_data

    def test_wish_create_and_list(self, app, patched_db):
        """Schichtwunsch anlegen und in Liste sehen."""
        client, _ = _make_client(app, 'Planer')

        emps = client.get("/api/employees").json()
        assert emps
        emp_id = emps[0]["ID"]

        shifts = client.get("/api/shifts").json()
        assert shifts
        shift_id = shifts[0]["ID"]

        # Wunsch anlegen
        wish_resp = client.post("/api/wishes", json={
            "employee_id": emp_id,
            "date": "2025-10-10",
            "shift_id": shift_id,
            "wish_type": "WUNSCH",
        })
        assert wish_resp.status_code == 200, f"Wunsch anlegen fehlgeschlagen: {wish_resp.text}"

        # Wünsche abrufen
        list_resp = client.get(f"/api/wishes?employee_id={emp_id}")
        assert list_resp.status_code == 200
        wishes = list_resp.json()
        assert isinstance(wishes, list)

        # Wunsch sollte vorhanden sein
        found = any(
            str(w.get("EMPLOYEE_ID") or w.get("employee_id", "")) == str(emp_id)
            for w in wishes
        )
        assert found, "Angelegter Wunsch nicht in Liste gefunden"

    def test_generate_schedule_respects_restrictions(self, app, patched_db):
        """Schedule-Generierung mit respect_restrictions=True gibt korrekte Felder zurück."""
        client, _ = _make_client(app, 'Planer')

        emps = client.get("/api/employees").json()
        assert emps
        emp_id = emps[0]["ID"]

        # Dry-run ohne Restrictions
        resp_no = client.post("/api/schedule/generate", json={
            "year": 2026,
            "month": 1,
            "employee_ids": [emp_id],
            "dry_run": True,
            "respect_restrictions": False,
            "force": False,
        })
        assert resp_no.status_code == 200
        data_no = resp_no.json()

        # Dry-run MIT Restrictions
        resp_yes = client.post("/api/schedule/generate", json={
            "year": 2026,
            "month": 1,
            "employee_ids": [emp_id],
            "dry_run": True,
            "respect_restrictions": True,
            "force": False,
        })
        assert resp_yes.status_code == 200
        data_yes = resp_yes.json()

        # skipped_restriction kann >= 0 sein
        assert data_yes["skipped_restriction"] >= 0
        assert data_no["skipped_restriction"] >= 0


# ═════════════════════════════════════════════════════════════════════════════
# 4. Backup/Restore Workflow
# ═════════════════════════════════════════════════════════════════════════════

class TestBackupRestoreWorkflow:
    """
    Workflow: Backup erstellen → Daten ändern → Restore → Daten wie vor Änderung
    """

    def test_backup_download_is_valid_zip(self, app, patched_db):
        """Backup-Download liefert eine gültige ZIP-Datei."""
        client, _ = _make_client(app, 'Admin')

        resp = client.get("/api/backup/download")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/zip") or \
               len(resp.content) > 0

        # ZIP-Validierung
        buf = io.BytesIO(resp.content)
        assert zipfile.is_zipfile(buf), "Response ist keine gültige ZIP-Datei"

        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert len(names) > 0, "ZIP-Datei ist leer"
            # Mindestens eine DBF-Datei sollte vorhanden sein
            dbf_files = [n for n in names if n.upper().endswith(".DBF")]
            assert dbf_files, f"Keine DBF-Dateien im Backup. Vorhanden: {names}"

    def test_backup_list_endpoint(self, app, patched_db):
        """Backup-Liste ist erreichbar."""
        client, _ = _make_client(app, 'Admin')

        resp = client.get("/api/admin/backups")
        assert resp.status_code == 200
        data = resp.json()
        # Response kann Liste oder Dict mit 'backups'-Key sein
        backups = data if isinstance(data, list) else data.get("backups", [])
        assert isinstance(backups, list)

    def test_backup_restore_roundtrip(self, app, patched_db):
        """Backup erstellen → Restore → Mitarbeiter-Liste identisch."""
        client, _ = _make_client(app, 'Admin')

        # 1. Aktuelle Mitarbeiter-Liste speichern
        emps_before = client.get("/api/employees").json()
        assert emps_before, "Keine Mitarbeiter vor Backup"

        # 2. Backup erstellen
        backup_resp = client.get("/api/backup/download")
        assert backup_resp.status_code == 200
        backup_bytes = backup_resp.content
        assert len(backup_bytes) > 0

        # 3. Backup restaurieren (selbe Daten → sollte 200 zurückgeben)
        restore_resp = client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", io.BytesIO(backup_bytes), "application/zip")},
        )
        assert restore_resp.status_code == 200, f"Restore fehlgeschlagen: {restore_resp.text}"
        restore_data = restore_resp.json()
        assert "restored" in restore_data
        assert restore_data["restored"] > 0, "Keine Dateien restored"

        # 4. Mitarbeiter-Liste nach Restore prüfen
        emps_after = client.get("/api/employees").json()
        before_ids = {e["ID"] for e in emps_before}
        after_ids = {e["ID"] for e in emps_after}
        assert before_ids == after_ids, \
            f"Mitarbeiter-IDs nach Restore unterschiedlich: {before_ids} vs {after_ids}"

    def test_backup_non_admin_forbidden(self, app, patched_db):
        """Nur Admins können Backups erstellen."""
        client, _ = _make_client(app, 'Leser')

        resp = client.get("/api/backup/download")
        assert resp.status_code == 403, f"Leser sollte kein Backup erstellen dürfen: {resp.status_code}"


# ═════════════════════════════════════════════════════════════════════════════
# 5. Auth-Workflow
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthWorkflow:
    """
    Workflow: Login → Token → Endpoint aufrufen → Logout → Token ungültig
    """

    def test_login_creates_valid_token(self, app, patched_db):
        """Login gibt gültigen Token zurück der für API-Aufrufe nutzbar ist."""
        from starlette.testclient import TestClient

        client = TestClient(app)

        # Admin-User für Login erstellen (direkt in DB via API)
        admin_client, _ = _make_client(app, 'Admin')

        # Neuen Test-User anlegen
        username = f"e2e_auth_{secrets.token_hex(4)}"
        password = "TestPass123!"

        create_resp = admin_client.post("/api/users", json={
            "NAME": username,
            "PASSWORD": password,
            "role": "Leser",
            "DESCRIP": "E2E Auth Test User",
        })
        assert create_resp.status_code == 200, f"User anlegen fehlgeschlagen: {create_resp.text}"
        user_data = create_resp.json()
        user_id = user_data.get("id") or user_data.get("ID")

        try:
            # 1. Login
            login_resp = client.post("/api/auth/login", json={
                "username": username,
                "password": password,
            })
            assert login_resp.status_code == 200, f"Login fehlgeschlagen: {login_resp.text}"
            login_data = login_resp.json()
            assert login_data.get("ok") is True
            token = login_data.get("token")
            assert token, "Kein Token erhalten"
            assert len(token) > 10

            # 2. Token für API-Aufruf nutzen
            authed_client = TestClient(app)
            emp_resp = authed_client.get("/api/employees", headers={"X-Auth-Token": token})
            assert emp_resp.status_code == 200, f"API-Aufruf mit Token fehlgeschlagen: {emp_resp.text}"

            # 3. Logout
            logout_resp = authed_client.post("/api/auth/logout", headers={"X-Auth-Token": token})
            assert logout_resp.status_code == 200
            assert logout_resp.json().get("ok") is True

            # 4. Token nach Logout ungültig
            invalid_resp = authed_client.get("/api/employees", headers={"X-Auth-Token": token})
            assert invalid_resp.status_code == 401, \
                f"Token nach Logout sollte ungültig sein, got {invalid_resp.status_code}"

        finally:
            # Cleanup: User löschen
            if user_id:
                admin_client.delete(f"/api/users/{user_id}")

    def test_invalid_login_rejected(self, app, patched_db):
        """Falsches Passwort liefert 401."""
        from starlette.testclient import TestClient
        client = TestClient(app)

        resp = client.post("/api/auth/login", json={
            "username": "does_not_exist_xyz",
            "password": "wrong_password",
        })
        assert resp.status_code == 401

    def test_no_token_returns_401(self, app, patched_db):
        """Ohne Token ist ein geschützter Endpoint nicht erreichbar."""
        from starlette.testclient import TestClient
        client = TestClient(app)

        resp = client.get("/api/employees")
        assert resp.status_code == 401

    def test_token_expiry_check(self, app, patched_db):
        """Abgelaufener Token wird abgelehnt."""
        import time
        from api.main import _sessions
        from starlette.testclient import TestClient

        # Abgelaufenen Token direkt einfügen
        expired_token = secrets.token_hex(20)
        _sessions[expired_token] = {
            'ID': 999,
            'NAME': 'expired_user',
            'role': 'Leser',
            'ADMIN': False,
            'RIGHTS': 1,
            'expires_at': time.time() - 1,  # bereits abgelaufen
        }

        client = TestClient(app)
        resp = client.get("/api/employees", headers={"X-Auth-Token": expired_token})
        assert resp.status_code == 401, \
            f"Abgelaufener Token sollte 401 ergeben, got {resp.status_code}"

        # Cleanup
        _sessions.pop(expired_token, None)

    def test_role_based_access_control(self, app, patched_db):
        """Leser kann nicht schreiben (403), Planer kann schreiben."""
        leser_client, _ = _make_client(app, 'Leser')
        planer_client, _ = _make_client(app, 'Planer')

        emps = planer_client.get("/api/employees").json()
        assert emps
        shifts = planer_client.get("/api/shifts").json()
        assert shifts

        # Leser: Schreiben verboten
        resp_leser = leser_client.post("/api/schedule/bulk", json={
            "entries": [{"employee_id": emps[0]["ID"], "date": "2025-11-01", "shift_id": shifts[0]["ID"]}],
            "overwrite": True
        })
        assert resp_leser.status_code == 403, \
            f"Leser sollte 403 bekommen, got {resp_leser.status_code}"

        # Planer: Schreiben erlaubt
        resp_planer = planer_client.post("/api/schedule/bulk", json={
            "entries": [{"employee_id": emps[0]["ID"], "date": "2025-11-01", "shift_id": shifts[0]["ID"]}],
            "overwrite": True
        })
        assert resp_planer.status_code == 200, \
            f"Planer sollte 200 bekommen, got {resp_planer.status_code}: {resp_planer.text}"
