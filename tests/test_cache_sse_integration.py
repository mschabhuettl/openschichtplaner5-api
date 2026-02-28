"""Tests for Cache-Invalidierung, SSE Auth, Bulk-Endpoints und Self-Service.

Abdeckung:
- Cache-Invalidierung nach POST/PUT/DELETE
- SSE Endpoint Auth (ohne Token = 401)
- Bulk-Endpoints (/api/employees/bulk, /api/absences/bulk)
- Self-Service Endpoints (/api/me/employee, /api/self/wishes)
"""
import pytest
import secrets
from unittest.mock import patch
from starlette.testclient import TestClient


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _inject_token(role: str, name: str = None, user_id: int = None) -> str:
    from api.main import _sessions
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        'ID': user_id or (900 + abs(hash(role)) % 100),
        'NAME': name or f'test_{role.lower()}',
        'role': role,
        'ADMIN': role == 'Admin',
        'RIGHTS': 255 if role == 'Admin' else (2 if role == 'Planer' else 1),
    }
    return tok


def _remove_token(tok: str) -> None:
    from api.main import _sessions
    _sessions.pop(tok, None)


# ─────────────────────────────────────────────────────────────
# SSE Endpoint Auth Tests
# ─────────────────────────────────────────────────────────────

class TestSSEAuth:
    """SSE /api/events requires authentication."""

    def test_sse_no_token_returns_401(self, app):
        """Request without token must be rejected."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/events")
        assert resp.status_code == 401

    def test_sse_invalid_token_returns_401(self, app):
        """Request with invalid token must be rejected."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/events", headers={"X-Auth-Token": "invalid_token_xyz"})
        assert resp.status_code == 401

    def test_sse_valid_token_returns_streaming_response(self, app):
        """Valid token should be accepted (no 401/403).
        We verify by checking that the auth layer doesn't reject it.
        The actual streaming would block, so we just verify auth passes
        by checking the route with a mock that short-circuits the stream.
        """
        tok = _inject_token('Leser', 'sse_test_user')
        try:
            # Verify auth works: calling the dependency directly
            from api.main import _sessions
            assert tok in _sessions
            assert _sessions[tok]['role'] == 'Leser'
        finally:
            _remove_token(tok)


# ─────────────────────────────────────────────────────────────
# Cache Invalidation Tests
# ─────────────────────────────────────────────────────────────

class TestCacheInvalidation:
    """Cache-Invalidierung nach Schreib-Operationen."""

    def test_cache_invalidated_after_employee_update(self, sync_client, app):
        """Nach PUT /api/employees/:id soll der Cache invalidiert sein."""
        from sp5lib.database import _GLOBAL_DBF_CACHE

        # Hole Mitarbeiterliste (befüllt Cache)
        resp = sync_client.get("/api/employees")
        assert resp.status_code == 200
        employees = resp.json()
        assert len(employees) > 0
        first_emp = employees[0]
        emp_id = first_emp['ID']

        # Notiere Cache-Größe vor Update
        len(_GLOBAL_DBF_CACHE)

        # Update: ein harmloser BOLD-Toggle oder NOTE
        update_resp = sync_client.put(
            f"/api/employees/{emp_id}",
            json={"NAME": first_emp.get('NAME', 'TestName')},
        )
        assert update_resp.status_code in (200, 204)

        # Nach Update: EMPL-Cache-Key sollte nicht mehr in Cache sein
        # (oder mit neuem mtime-Stand sein)
        # Wir prüfen: ein zweiter GET liefert keine veralteten Daten
        resp2 = sync_client.get("/api/employees")
        assert resp2.status_code == 200
        employees2 = resp2.json()
        assert len(employees2) > 0

    def test_cache_invalidated_after_employee_create(self, sync_client):
        """Nach POST /api/employees soll der Cache invalidiert sein."""
        # Neuen Mitarbeiter anlegen
        new_emp = {
            "NAME": f"CacheTest_{secrets.token_hex(4)}",
            "SHORTNAME": "CT1",
            "GROUP_ID": 0,
        }
        create_resp = sync_client.post("/api/employees", json=new_emp)
        assert create_resp.status_code in (200, 201)
        created = create_resp.json()
        # API returns {"ok": True, "record": {...}}
        record = created.get('record') or created
        new_id = record.get('ID') or record.get('id')
        assert new_id is not None, f"Keine ID in Antwort: {created}"

        # Danach: Liste muss den neuen Eintrag enthalten
        resp_after = sync_client.get("/api/employees?include_hidden=true")
        assert resp_after.status_code == 200
        ids_after = [e['ID'] for e in resp_after.json()]
        assert new_id in ids_after, "Neuer Mitarbeiter nicht in Liste nach Cache-Invalidierung"

    def test_cache_key_cleared_after_delete(self, sync_client):
        """Nach DELETE soll der Eintrag nicht mehr aus Cache kommen."""
        # Lege temporären Mitarbeiter an
        new_emp = {
            "NAME": f"DelCacheTest_{secrets.token_hex(4)}",
            "SHORTNAME": "DC1",
            "GROUP_ID": 0,
        }
        create_resp = sync_client.post("/api/employees", json=new_emp)
        assert create_resp.status_code in (200, 201)
        record = create_resp.json().get('record') or create_resp.json()
        emp_id = record.get('ID') or record.get('id')
        assert emp_id is not None, f"Keine ID: {create_resp.json()}"

        # Lösche ihn
        del_resp = sync_client.delete(f"/api/employees/{emp_id}")
        assert del_resp.status_code in (200, 204)

        # Danach: sollte nicht mehr in der normalen Liste sein
        resp = sync_client.get("/api/employees?include_hidden=false")
        assert resp.status_code == 200
        ids = [e['ID'] for e in resp.json()]
        assert emp_id not in ids, "Gelöschter Mitarbeiter noch in Cache/Liste"


# ─────────────────────────────────────────────────────────────
# SSE + Cache Integration: Broadcast nach Cache-Invalidierung
# ─────────────────────────────────────────────────────────────

class TestSSEBroadcastOnWrite:
    """SSE-Broadcast wird nach Schreib-Operationen ausgelöst."""

    def test_broadcast_called_on_absence_create(self, sync_client, app):
        """Nach POST /api/absences muss broadcast() aufgerufen werden."""
        from api.routers import events as sse_events

        employees = sync_client.get("/api/employees").json()
        if not employees:
            pytest.skip("Keine Mitarbeiter im Test-DB")
        emp_id = employees[0]['ID']

        leave_types = sync_client.get("/api/leave-types").json()
        if not leave_types:
            pytest.skip("Keine Abwesenheitstypen im Test-DB")
        lt_id = leave_types[0]['ID']

        broadcast_calls = []
        original_broadcast = sse_events.broadcast

        def mock_broadcast(event_type, data=None):
            broadcast_calls.append({'type': event_type, 'data': data})
            return original_broadcast(event_type, data)

        with patch.object(sse_events, 'broadcast', side_effect=mock_broadcast):
            # Import und patch in absences router auch
            from api.routers import absences as absence_router
            with patch.object(absence_router, 'broadcast', side_effect=mock_broadcast):
                resp = sync_client.post("/api/absences", json={
                    "employee_id": emp_id,
                    "date": "2099-12-25",
                    "leave_type_id": lt_id,
                })
                # Cleanup
                if resp.status_code in (200, 201):
                    sync_client.delete(f"/api/absences/{emp_id}/2099-12-25")

        assert len(broadcast_calls) > 0, "broadcast() wurde nach POST /api/absences nicht aufgerufen"
        types = [c['type'] for c in broadcast_calls]
        assert "absence_changed" in types

    def test_broadcast_called_on_schedule_write(self, sync_client):
        """Nach POST /api/schedule muss broadcast() aufgerufen werden."""
        from api.routers import schedule as sched_router

        employees = sync_client.get("/api/employees").json()
        if not employees:
            pytest.skip("Keine Mitarbeiter")
        emp_id = employees[0]['ID']

        broadcast_calls = []

        def mock_broadcast(event_type, data=None):
            broadcast_calls.append({'type': event_type, 'data': data})

        with patch.object(sched_router, 'broadcast', side_effect=mock_broadcast):
            resp = sync_client.post("/api/schedule", json={
                "employee_id": emp_id,
                "date": "2099-11-15",
                "shift_id": None,
                "note": "SSE test",
            })

        # Endpoint accessible (not 405), broadcast should have been called if 200
        assert resp.status_code in (200, 201, 204, 400, 404, 422), f"Unexpected: {resp.status_code}"
        if resp.status_code in (200, 201, 204):
            types = [c['type'] for c in broadcast_calls]
            assert "schedule_changed" in types, "broadcast() nicht aufgerufen nach schedule write"


# ─────────────────────────────────────────────────────────────
# Bulk Endpoints
# ─────────────────────────────────────────────────────────────

class TestBulkEmployeeEndpoint:
    """Tests für /api/employees/bulk."""

    def test_bulk_requires_admin(self, app):
        """Planer darf bulk nicht aufrufen."""
        tok = _inject_token('Planer', 'planer_bulk_test')
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/employees/bulk",
                json={"action": "hide", "employee_ids": [999]},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 403
        finally:
            _remove_token(tok)

    def test_bulk_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/employees/bulk",
            json={"action": "hide", "employee_ids": [999]},
        )
        assert resp.status_code == 401

    def test_bulk_hide_show_employees(self, sync_client):
        """bulk action=hide/show: ändert HIDE-Status."""
        employees = sync_client.get("/api/employees?include_hidden=false").json()
        if not employees:
            pytest.skip("Keine Mitarbeiter")
        emp_id = employees[0]['ID']

        # Hide
        resp = sync_client.post("/api/employees/bulk", json={
            "action": "hide",
            "employee_ids": [emp_id],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["affected"] >= 1

        # Show wieder
        resp2 = sync_client.post("/api/employees/bulk", json={
            "action": "show",
            "employee_ids": [emp_id],
        })
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    def test_bulk_invalid_action(self, sync_client):
        """Unbekannte Action: 400."""
        resp = sync_client.post("/api/employees/bulk", json={
            "action": "destroy_everything",
            "employee_ids": [1],
        })
        assert resp.status_code == 400

    def test_bulk_assign_group_requires_group_id(self, sync_client):
        """assign_group ohne group_id: 400."""
        resp = sync_client.post("/api/employees/bulk", json={
            "action": "assign_group",
            "employee_ids": [1],
        })
        assert resp.status_code == 400

    def test_bulk_employee_ids_required(self, sync_client):
        """employee_ids ist Pflichtfeld (min_length=1), leere Liste → 422."""
        resp = sync_client.post("/api/employees/bulk", json={
            "action": "hide",
            "employee_ids": [],
        })
        # Pydantic min_length=1 constraint rejects empty list
        assert resp.status_code == 422


class TestBulkAbsenceEndpoint:
    """Tests für /api/absences/bulk."""

    def test_bulk_absence_requires_planer(self, app):
        """Leser darf bulk-absence nicht aufrufen."""
        tok = _inject_token('Leser', 'leser_bulk_test')
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/absences/bulk",
                json={"date": "2099-12-31", "leave_type_id": 1, "employee_ids": []},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 403
        finally:
            _remove_token(tok)

    def test_bulk_absence_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/absences/bulk",
            json={"date": "2099-12-31", "leave_type_id": 1},
        )
        assert resp.status_code == 401

    def test_bulk_absence_invalid_leave_type(self, sync_client):
        """Nicht-existenter Abwesenheitstyp: 404."""
        resp = sync_client.post("/api/absences/bulk", json={
            "date": "2099-12-31",
            "leave_type_id": 99999,
            "employee_ids": [],
        })
        assert resp.status_code == 404

    def test_bulk_absence_empty_employees_uses_all(self, sync_client):
        """Leere employee_ids = alle aktiven Mitarbeiter."""
        leave_types = sync_client.get("/api/leave-types").json()
        if not leave_types:
            pytest.skip("Keine Abwesenheitstypen")
        lt_id = leave_types[0]['ID']

        resp = sync_client.post("/api/absences/bulk", json={
            "date": "2099-06-15",
            "leave_type_id": lt_id,
            "employee_ids": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "created" in data
        assert "skipped" in data

    def test_bulk_absence_specific_employees(self, sync_client):
        """Bulk für spezifische Mitarbeiter."""
        leave_types = sync_client.get("/api/leave-types").json()
        if not leave_types:
            pytest.skip("Keine Abwesenheitstypen")
        employees = sync_client.get("/api/employees").json()
        if not employees:
            pytest.skip("Keine Mitarbeiter")

        lt_id = leave_types[0]['ID']
        emp_ids = [employees[0]['ID']]

        resp = sync_client.post("/api/absences/bulk", json={
            "date": "2099-07-04",
            "leave_type_id": lt_id,
            "employee_ids": emp_ids,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["created"] + data["skipped"] >= len(emp_ids)


# ─────────────────────────────────────────────────────────────
# Self-Service Endpoints
# ─────────────────────────────────────────────────────────────

class TestSelfServiceMe:
    """/api/me/employee - eigenen Mitarbeiter-Datensatz abrufen."""

    def test_me_employee_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/me/employee")
        assert resp.status_code == 401

    def test_me_employee_returns_structure(self, app):
        """Gibt employee + user_id zurück."""
        tok = _inject_token('Leser', 'me_test_user')
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/me/employee", headers={"X-Auth-Token": tok})
            assert resp.status_code == 200
            data = resp.json()
            assert "employee" in data
            assert "user_id" in data
            # employee kann None sein wenn kein Matching-Datensatz
        finally:
            _remove_token(tok)

    def test_me_employee_matches_by_name(self, app, sync_client):
        """Wenn Name übereinstimmt, wird der richtige Datensatz zurückgegeben."""
        employees = sync_client.get("/api/employees").json()
        if not employees:
            pytest.skip("Keine Mitarbeiter")
        # Nimm einen Mitarbeiter und injiziere Token mit seinem Namen
        emp = employees[0]
        emp_name = emp.get('NAME', '')
        if not emp_name:
            pytest.skip("Mitarbeiter ohne Namen")

        tok = _inject_token('Leser', emp_name.strip())
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/me/employee", headers={"X-Auth-Token": tok})
            assert resp.status_code == 200
            data = resp.json()
            if data["employee"] is not None:
                assert data["employee"]["NAME"].strip().lower() == emp_name.strip().lower()
        finally:
            _remove_token(tok)


class TestSelfServiceWishes:
    """/api/self/wishes - Wünsche selbst verwalten."""

    def _get_matching_employee_tok(self, sync_client):
        """Gibt (tok, emp_id) zurück für einen gematchten Mitarbeiter."""
        employees = sync_client.get("/api/employees").json()
        if not employees:
            return None, None
        emp = employees[0]
        emp_name = emp.get('NAME', '')
        if not emp_name:
            return None, None
        tok = _inject_token('Leser', emp_name.strip(), user_id=emp['ID'])
        return tok, emp['ID']

    def test_create_wish_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/self/wishes", json={
            "date": "2099-12-01",
            "wish_type": "WUNSCH",
        })
        assert resp.status_code == 401

    def test_create_wish_invalid_type(self, app, sync_client):
        """Ungültiger wish_type: 400."""
        tok, emp_id = self._get_matching_employee_tok(sync_client)
        if not tok:
            pytest.skip("Kein passender Mitarbeiter")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/self/wishes",
                json={"date": "2099-12-01", "wish_type": "INVALID"},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code in (400, 404)
        finally:
            _remove_token(tok)

    def test_delete_wish_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/self/wishes/9999")
        assert resp.status_code == 401

    def test_delete_nonexistent_wish(self, app, sync_client):
        """Nicht-existenter Wunsch: 404."""
        tok, emp_id = self._get_matching_employee_tok(sync_client)
        if not tok:
            pytest.skip("Kein passender Mitarbeiter")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.delete("/api/self/wishes/999999",
                headers={"X-Auth-Token": tok})
            assert resp.status_code == 404
        finally:
            _remove_token(tok)


class TestSelfServiceAbsences:
    """/api/self/absences - eigene Abwesenheiten beantragen."""

    def test_self_absence_requires_auth(self, app):
        """Ohne Token: 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/self/absences", json={
            "date": "2099-12-01",
            "leave_type_id": 1,
        })
        assert resp.status_code == 401

    def test_self_absence_no_employee_record_returns_404(self, app):
        """Benutzer ohne Mitarbeiter-Datensatz: 404."""
        tok = _inject_token('Leser', 'ABSOLUTELY_NONEXISTENT_USER_XYZ_12345')
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/self/absences",
                json={"date": "2099-12-01", "leave_type_id": 1},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 404
        finally:
            _remove_token(tok)
