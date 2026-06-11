"""G-3: Schutz des eingebauten Admin-Kontos (Spec 9.3 Nr. 3).

Das Konto "Admin" (ID 251) ist weder lösch- noch umbenennbar und kann nicht
herabgestuft werden; der letzte verbliebene Administrator ist ebenfalls
geschützt. In der Fixture-DB ist ID 251 zugleich der einzige Administrator.
"""

ADMIN_ID = 251


class TestProtectedAdminAccount:
    def test_delete_admin_account_403(self, admin_client):
        resp = admin_client.delete(f"/api/users/{ADMIN_ID}")
        assert resp.status_code == 403
        assert "Admin" in resp.json()["detail"]
        # weiterhin vorhanden
        users = admin_client.get("/api/users").json()
        assert any(u["ID"] == ADMIN_ID for u in users)

    def test_rename_admin_account_403(self, admin_client):
        resp = admin_client.put(f"/api/users/{ADMIN_ID}", json={"NAME": "Boss"})
        assert resp.status_code == 403
        assert "umbenannt" in resp.json()["detail"]

    def test_demote_admin_account_403(self, admin_client):
        resp = admin_client.put(f"/api/users/{ADMIN_ID}", json={"role": "Leser"})
        assert resp.status_code == 403
        assert "herabgestuft" in resp.json()["detail"]

    def test_admin_account_descrip_change_allowed(self, admin_client):
        """Nur Löschen/Umbenennen/Herabstufen ist gesperrt — andere Felder nicht."""
        resp = admin_client.put(
            f"/api/users/{ADMIN_ID}", json={"DESCRIP": "Systemkonto"}
        )
        assert resp.status_code == 200

    def test_same_name_update_allowed(self, admin_client):
        """NAME unverändert mitschicken ist keine Umbenennung."""
        resp = admin_client.put(f"/api/users/{ADMIN_ID}", json={"NAME": "Admin"})
        assert resp.status_code == 200


class TestLastAdminProtection:
    def _create_second_admin(self, client):
        resp = client.post(
            "/api/users",
            json={"NAME": "zweiter_admin", "PASSWORD": "Geheim123", "role": "Admin"},
        )
        assert resp.status_code == 200
        return resp.json()["record"]["ID"]

    def test_second_admin_can_be_deleted_and_demoted(self, admin_client):
        """Solange ein weiterer Admin existiert, greift nur der 'Admin'-Schutz."""
        uid = self._create_second_admin(admin_client)
        resp = admin_client.put(f"/api/users/{uid}", json={"role": "Planer"})
        assert resp.status_code == 200
        # wieder befördern und löschen
        admin_client.put(f"/api/users/{uid}", json={"role": "Admin"})
        resp = admin_client.delete(f"/api/users/{uid}")
        assert resp.status_code == 200

    def test_last_admin_protected_after_others_removed(self, admin_client):
        """Wird der Zweit-Admin gelöscht, ist ID 251 wieder der letzte Admin."""
        uid = self._create_second_admin(admin_client)
        assert admin_client.delete(f"/api/users/{uid}").status_code == 200
        resp = admin_client.delete(f"/api/users/{ADMIN_ID}")
        assert resp.status_code == 403

    def test_normal_users_unaffected(self, admin_client):
        users = admin_client.get("/api/users").json()
        normal = next(u for u in users if not u["ADMIN"])
        resp = admin_client.put(
            f"/api/users/{normal['ID']}", json={"DESCRIP": "geändert"}
        )
        assert resp.status_code == 200
        resp = admin_client.delete(f"/api/users/{normal['ID']}")
        assert resp.status_code == 200
