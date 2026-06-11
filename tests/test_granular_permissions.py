"""G-1 (api-Seite): granulare 5USER-Rechte (Spec 9.6) — /api/auth/me liefert
ein permissions-Objekt, und die Schreibrouten erzwingen die Flags:

* Dienste/Schedule-Writes → WDUTIES (Diensttausch zusätzlich WSWAPONLY)
* Abwesenheits-Writes → WABSENCES
* Überstunden/Buchungs-Writes → WOVERTIMES
* Notiz/Kommentar-Writes → WNOTES
* Abweichungs-Writes → WDEVIATION
* Modellzuordnungs-Writes → WCYCLEASS
* Mitarbeiter-Anlegen → ADDEMPL (Opt-in, Spec 9.5.3 Nr. 2.1)
* WPAST=0 ⇒ Writes mit Datum < heute → 403

Sessions ohne Flags (Legacy/Test-Fixtures) bleiben unbeschränkt;
Admin und Dev-Mode unverändert.
"""

import secrets
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

PERMISSION_KEYS = {
    "wduties", "wabsences", "wovertimes", "wnotes", "wdeviation",
    "wcycleass", "wswaponly", "wpast", "addempl", "showabs",
    "shownotes", "showstats", "backup",
}


def _inject(role="Planer", name=None, **flags):
    """Planer-Session mit expliziten granularen Flags injizieren."""
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 950,
        "NAME": name or "granular_planer",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 2,
        **flags,
    }
    return tok


@pytest.fixture
def client(write_db_path, app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _h(tok):
    return {"X-Auth-Token": tok}


class TestAuthMePermissions:
    def test_admin_all_true(self, client):
        from sp5api.main import _sessions

        tok = _inject(role="Admin", name="perm_admin")
        try:
            data = client.get("/api/auth/me", headers=_h(tok)).json()
            assert set(data["permissions"]) == PERMISSION_KEYS
            assert all(data["permissions"].values())
        finally:
            _sessions.pop(tok, None)

    def test_planer_fallback_defaults(self, client):
        """Session ohne 5USER-Satz: Schreib-Flags nach Rolle, Opt-ins aus."""
        from sp5api.main import _sessions

        tok = _inject()
        try:
            perms = client.get("/api/auth/me", headers=_h(tok)).json()["permissions"]
            assert perms["wduties"] is True
            assert perms["wpast"] is True
            assert perms["wswaponly"] is False
            assert perms["addempl"] is False
            assert perms["backup"] is False
            assert perms["shownotes"] is True
        finally:
            _sessions.pop(tok, None)

    def test_session_flags_win_over_defaults(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDUTIES=False, ADDEMPL=True)
        try:
            perms = client.get("/api/auth/me", headers=_h(tok)).json()["permissions"]
            assert perms["wduties"] is False
            assert perms["addempl"] is True
        finally:
            _sessions.pop(tok, None)

    def test_real_user_record_is_used(self, client):
        """Echte Logins: permissions kommen aus dem 5USER-Record."""
        from sp5api.main import _sessions

        admin_tok = _inject(role="Admin", name="perm_admin2")
        try:
            created = client.post(
                "/api/users",
                json={"NAME": "granular_real", "PASSWORD": "Geheim123",
                      "role": "Planer"},
                headers=_h(admin_tok),
            ).json()["record"]
            login = client.post(
                "/api/auth/login",
                json={"username": "granular_real", "password": "Geheim123"},
            ).json()
            perms = client.get(
                "/api/auth/me", headers={"X-Auth-Token": login["token"]}
            ).json()["permissions"]
            # create_user-Defaults für Planer: Schreibflags 1, ADDEMPL 0
            assert perms["wduties"] is True
            assert perms["addempl"] is False
            assert created["ID"] is not None
        finally:
            _sessions.pop(admin_tok, None)


class TestWriteFlagEnforcement:
    def _expect_403(self, client, tok, method, url, **kwargs):
        resp = getattr(client, method)(url, headers=_h(tok), **kwargs)
        assert resp.status_code == 403, f"{url}: {resp.status_code} {resp.text}"

    def test_wduties_blocks_schedule_writes(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDUTIES=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/schedule",
                json={"employee_id": 40, "date": "2027-06-01", "shift_id": 1},
            )
            self._expect_403(client, tok, "delete", "/api/schedule/40/2027-06-01")
            self._expect_403(client, tok, "delete", "/api/schedule-shift/40/2027-06-01")
        finally:
            _sessions.pop(tok, None)

    def test_wduties_true_allows_schedule_write(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDUTIES=True)
        try:
            shift_id = client.get("/api/shifts", headers=_h(tok)).json()[0]["ID"]
            resp = client.post(
                "/api/schedule",
                json={"employee_id": 40, "date": "2027-06-01", "shift_id": shift_id},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)

    def test_wabsences_blocks_absence_writes(self, client):
        from sp5api.main import _sessions

        tok = _inject(WABSENCES=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/absences",
                json={"employee_id": 40, "date": "2027-06-01", "leave_type_id": 1},
            )
            self._expect_403(
                client, tok, "put", "/api/absences/40/2027-06-01",
                json={"interval": 1},
            )
            self._expect_403(client, tok, "delete", "/api/absences/40/2027-06-01")
            self._expect_403(
                client, tok, "post", "/api/absences/bulk",
                json={"date": "2027-06-01", "leave_type_id": 1, "employee_ids": [40]},
            )
        finally:
            _sessions.pop(tok, None)

    def test_wovertimes_blocks_booking_writes(self, client):
        from sp5api.main import _sessions

        tok = _inject(WOVERTIMES=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/bookings",
                json={"employee_id": 40, "date": "2027-06-01", "type": 0, "value": 1.0},
            )
            self._expect_403(client, tok, "delete", "/api/bookings/1")
        finally:
            _sessions.pop(tok, None)

    def test_wnotes_blocks_note_and_comment_writes(self, client):
        from sp5api.main import _sessions

        tok = _inject(WNOTES=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/notes",
                json={"date": "2027-06-01", "text": "x"},
            )
            self._expect_403(client, tok, "delete", "/api/notes/1")
            self._expect_403(
                client, tok, "post", "/api/schedule/comments",
                json={"date": "2027-06-01", "group_id": 0, "text": "x"},
            )
        finally:
            _sessions.pop(tok, None)

    def test_wdeviation_blocks_deviation_write(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDEVIATION=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/einsatzplan/deviation",
                json={"employee_id": 40, "date": "2027-06-01",
                      "name": "Abw", "duration": 4.0},
            )
        finally:
            _sessions.pop(tok, None)

    def test_wcycleass_blocks_cycle_assignment(self, client):
        from sp5api.main import _sessions

        tok = _inject(WCYCLEASS=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/shift-cycles/assign",
                json={"employee_id": 40, "cycle_id": 1, "start_date": "2027-06-01"},
            )
            self._expect_403(client, tok, "delete", "/api/shift-cycles/assign/40")
        finally:
            _sessions.pop(tok, None)

    def test_wswaponly_allows_swap_despite_wduties_false(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDUTIES=False, WSWAPONLY=True)
        try:
            resp = client.post(
                "/api/schedule/swap",
                json={"employee_id_1": 40, "employee_id_2": 41,
                      "dates": ["2027-06-01"]},
                headers=_h(tok),
            )
            # darf nicht am Rechte-Check scheitern (Tausch leerer Tage ist ok)
            assert resp.status_code != 403, resp.text
        finally:
            _sessions.pop(tok, None)

    def test_swap_blocked_when_both_flags_false(self, client):
        from sp5api.main import _sessions

        tok = _inject(WDUTIES=False, WSWAPONLY=False)
        try:
            self._expect_403(
                client, tok, "post", "/api/schedule/swap",
                json={"employee_id_1": 40, "employee_id_2": 41,
                      "dates": ["2027-06-01"]},
            )
        finally:
            _sessions.pop(tok, None)

    def test_leser_still_blocked_even_with_flags(self, client):
        """Rollen-Gate bleibt: Leser darf trotz Flags nicht schreiben."""
        from sp5api.main import _sessions

        tok = _inject(role="Leser", WDUTIES=True)
        try:
            self._expect_403(
                client, tok, "post", "/api/schedule",
                json={"employee_id": 40, "date": "2027-06-01", "shift_id": 1},
            )
        finally:
            _sessions.pop(tok, None)


class TestAddEmplEnforcement:
    _BODY = {"NAME": "Perm", "FIRSTNAME": "Test", "SHORTNAME": "PT99"}

    def test_planer_without_addempl_403(self, client):
        from sp5api.main import _sessions

        tok = _inject()  # kein ADDEMPL-Flag → Opt-in fehlt
        try:
            resp = client.post("/api/employees", json=self._BODY, headers=_h(tok))
            assert resp.status_code == 403
        finally:
            _sessions.pop(tok, None)

    def test_planer_with_addempl_may_create(self, client):
        from sp5api.main import _sessions

        tok = _inject(ADDEMPL=True)
        try:
            resp = client.post("/api/employees", json=self._BODY, headers=_h(tok))
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)

    def test_admin_unaffected(self, client):
        from sp5api.main import _sessions

        tok = _inject(role="Admin", name="addempl_admin")
        try:
            resp = client.post(
                "/api/employees",
                json={**self._BODY, "SHORTNAME": "PT98"},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)


class TestWpastEnforcement:
    def test_wpast_false_blocks_past_writes(self, client):
        from sp5api.main import _sessions

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tok = _inject(WPAST=False)
        try:
            for method, url, kwargs in [
                ("post", "/api/absences",
                 {"json": {"employee_id": 40, "date": yesterday, "leave_type_id": 1}}),
                ("delete", f"/api/absences/40/{yesterday}", {}),
                ("post", "/api/schedule",
                 {"json": {"employee_id": 40, "date": yesterday, "shift_id": 1}}),
                ("delete", f"/api/schedule/40/{yesterday}", {}),
                ("delete", f"/api/schedule-shift/40/{yesterday}", {}),
            ]:
                resp = getattr(client, method)(url, headers=_h(tok), **kwargs)
                assert resp.status_code == 403, f"{url}: {resp.status_code}"
                assert "WPAST" in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_wpast_false_blocks_bulk_routes(self, client):
        """Sammelrouten prüfen WPAST je Eintrags-Datum (Phase 6)."""
        from sp5api.main import _sessions

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        tok = _inject(WPAST=False)
        try:
            for url, body in [
                ("/api/schedule/bulk",
                 # ein zulässiger + ein Vergangenheits-Eintrag → kompletter 403
                 {"entries": [
                     {"employee_id": 40, "date": tomorrow, "shift_id": 1},
                     {"employee_id": 40, "date": yesterday, "shift_id": 1},
                 ]}),
                ("/api/schedule/bulk-group",
                 {"employee_ids": [40], "shift_id": 1,
                  "date_from": yesterday, "date_to": tomorrow}),
                ("/api/schedule/copy-week",
                 {"source_employee_id": 40, "dates": [yesterday],
                  "target_employee_ids": [41]}),
                ("/api/schedule/swap",
                 {"employee_id_1": 40, "employee_id_2": 41, "dates": [yesterday]}),
                ("/api/einsatzplan",
                 {"employee_id": 40, "date": yesterday}),
                ("/api/einsatzplan/deviation",
                 {"employee_id": 40, "date": yesterday}),
                ("/api/bookings",
                 {"employee_id": 40, "date": yesterday, "type": 0, "value": 1.5}),
            ]:
                resp = client.post(url, json=body, headers=_h(tok))
                assert resp.status_code == 403, f"{url}: {resp.status_code} {resp.text}"
                assert "WPAST" in resp.text, url
        finally:
            _sessions.pop(tok, None)

    def test_wpast_false_blocks_writes_on_past_records(self, client):
        """Update/Delete per ID: das Datum des bestehenden Satzes zählt."""
        from sp5api.main import _sessions

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        priv = _inject(name="wpast_priv")  # ohne Flag → unbeschränkt
        restricted = _inject(WPAST=False, name="wpast_restricted")
        try:
            # Einsatzplan-Eintrag in der Vergangenheit anlegen (unbeschränkt)
            created = client.post(
                "/api/einsatzplan",
                json={"employee_id": 40, "date": yesterday, "name": "WPAST-Test"},
                headers=_h(priv),
            )
            assert created.status_code == 200, created.text
            spshi_id = created.json()["record"]["ID"]

            resp = client.put(
                f"/api/einsatzplan/{spshi_id}",
                json={"name": "geändert"},
                headers=_h(restricted),
            )
            assert resp.status_code == 403 and "WPAST" in resp.text
            resp = client.delete(f"/api/einsatzplan/{spshi_id}", headers=_h(restricted))
            assert resp.status_code == 403 and "WPAST" in resp.text
            # Aufräumen (unbeschränkt)
            assert client.delete(
                f"/api/einsatzplan/{spshi_id}", headers=_h(priv)
            ).status_code == 200

            # Buchung in der Vergangenheit anlegen (unbeschränkt)
            created = client.post(
                "/api/bookings",
                json={"employee_id": 40, "date": yesterday, "type": 0, "value": 1.0},
                headers=_h(priv),
            )
            assert created.status_code == 200, created.text
            booking_id = created.json()["record"]["id"]

            resp = client.delete(f"/api/bookings/{booking_id}", headers=_h(restricted))
            assert resp.status_code == 403 and "WPAST" in resp.text
            assert client.delete(
                f"/api/bookings/{booking_id}", headers=_h(priv)
            ).status_code == 200
        finally:
            _sessions.pop(priv, None)
            _sessions.pop(restricted, None)

    def test_wpast_false_blocks_swap_request_approval(self, client):
        """Die Genehmigung eines Tauschs in der Vergangenheit ist ein
        Plan-Write — WPAST greift VOR der Auflösung (Anfrage bleibt pending)."""
        from sp5api.main import _sessions

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        priv = _inject(name="swap_priv")
        restricted = _inject(WPAST=False, name="swap_restricted")
        try:
            created = client.post(
                "/api/swap-requests",
                json={
                    "requester_id": 40,
                    "requester_date": yesterday,
                    "partner_id": 41,
                    "partner_date": yesterday,
                    "note": "WPAST-Test",
                },
                headers=_h(priv),
            )
            assert created.status_code == 200, created.text
            swap_id = created.json()["id"]

            resp = client.patch(
                f"/api/swap-requests/{swap_id}/resolve",
                json={"action": "approve", "resolved_by": "restricted"},
                headers=_h(restricted),
            )
            assert resp.status_code == 403 and "WPAST" in resp.text

            # Anfrage ist NICHT aufgelöst worden — Reject zum Aufräumen klappt
            cleanup = client.patch(
                f"/api/swap-requests/{swap_id}/resolve",
                json={"action": "reject", "resolved_by": "priv"},
                headers=_h(priv),
            )
            assert cleanup.status_code == 200, cleanup.text
        finally:
            _sessions.pop(priv, None)
            _sessions.pop(restricted, None)

    def test_wpast_false_allows_future_bulk_writes(self, client):
        from sp5api.main import _sessions

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        tok = _inject(WPAST=False)
        try:
            shift_id = client.get("/api/shifts", headers=_h(tok)).json()[0]["ID"]
            resp = client.post(
                "/api/schedule/bulk",
                json={"entries": [
                    {"employee_id": 40, "date": tomorrow, "shift_id": shift_id}
                ]},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
            # Aufräumen über dieselbe Route (Zukunft, erlaubt)
            resp = client.post(
                "/api/schedule/bulk",
                json={"entries": [{"employee_id": 40, "date": tomorrow, "shift_id": None}]},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)

    def test_wpast_false_allows_future_writes(self, client):
        from sp5api.main import _sessions

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        tok = _inject(WPAST=False)
        try:
            lt_id = client.get("/api/leave-types", headers=_h(tok)).json()[0]["ID"]
            resp = client.post(
                "/api/absences",
                json={"employee_id": 40, "date": tomorrow, "leave_type_id": lt_id},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)

    def test_wpast_missing_allows_past_writes(self, client):
        """Sessions ohne Flag (Fixtures/Legacy) bleiben unbeschränkt."""
        from sp5api.main import _sessions

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tok = _inject()
        try:
            lt_id = client.get("/api/leave-types", headers=_h(tok)).json()[0]["ID"]
            resp = client.post(
                "/api/absences",
                json={"employee_id": 40, "date": yesterday, "leave_type_id": lt_id},
                headers=_h(tok),
            )
            assert resp.status_code == 200, resp.text
        finally:
            _sessions.pop(tok, None)
