"""V-17 Jahresabschluss-Optionen über die API (Spec 3.7.2, Dialog 6.8).

keep_entitlements = Dialog-Option "Urlaubsansprüche bleiben im Folgejahr
gleich" (R6.8-4); artspezifisches CARRYFWD (R6.8-5) wird beachtet.
Fixture-Arten: Urlaub (ID 1, CARRYFWD=1, STDENTIT=30),
Sonderurlaub (ID 14, CARRYFWD=0, STDENTIT=2).
"""

EMP = 40
YEAR = 2030  # Jahr ohne Bewegungsdaten in der Fixture-DB


def _seed(client):
    for lt_id, days, carry in ((1, 32.0, 2.0), (14, 2.0, 0.0)):
        resp = client.post(
            "/api/leave-entitlements",
            json={
                "employee_id": EMP,
                "year": YEAR,
                "days": days,
                "carry_forward": carry,
                "leave_type_id": lt_id,
            },
        )
        assert resp.status_code == 200


def _rows_next_year(client):
    rows = client.get(
        f"/api/leave-entitlements?year={YEAR + 1}&employee_id={EMP}"
    ).json()
    return {r["leave_type_id"]: r for r in rows}


class TestAnnualCloseKeepEntitlements:
    def test_preview_accepts_flag(self, write_client):
        _seed(write_client)
        prev = write_client.get(
            f"/api/annual-close/preview?year={YEAR}&keep_entitlements=true"
        ).json()
        # Urlaub: 32+2−0 = 34 Übertrag (kein Verbrauch im Fixture-Jahr 2030);
        # Sonderurlaub: 2 verfallen (CARRYFWD=0)
        assert prev["total_carry_forward"] == 34.0
        assert prev["total_forfeited"] == 2.0

    def test_run_with_keep_entitlements(self, write_client):
        _seed(write_client)
        resp = write_client.post(
            "/api/annual-close", json={"year": YEAR, "keep_entitlements": True}
        )
        assert resp.status_code == 200
        rows = _rows_next_year(write_client)
        # ENTITLEMNT kopiert (32 statt STDENTIT 30); Sonderurlaub mit REST=0
        assert rows[1]["entitlement"] == 32.0
        assert rows[1]["carry_forward"] == 34.0
        assert rows[14]["entitlement"] == 2.0
        assert rows[14]["carry_forward"] == 0.0

    def test_run_without_option_respects_carryfwd(self, write_client):
        _seed(write_client)
        resp = write_client.post("/api/annual-close", json={"year": YEAR})
        assert resp.status_code == 200
        rows = _rows_next_year(write_client)
        # Nur CARRYFWD-Arten werden fortgeschrieben, ENTITLEMNT aus STDENTIT
        assert rows[1]["entitlement"] == 30.0
        assert rows[1]["carry_forward"] == 34.0
        assert 14 not in rows

    def test_max_carry_forward_days_is_deprecated_noop(self, write_client):
        """D11: der Parameter suggerierte einen Übertrags-Deckel, den es seit
        Phase 3 nicht mehr gibt (Spec 3.7.2: ungedeckelt, nur CARRYFWD).
        Er bleibt aus Kompatibilität annehmbar, ist aber wirkungslos und im
        OpenAPI-Schema als deprecated markiert."""
        _seed(write_client)
        prev = write_client.get(
            f"/api/annual-close/preview?year={YEAR}"
            "&keep_entitlements=true&max_carry_forward_days=5"
        ).json()
        # kein Deckel: 34 Tage Übertrag trotz max_carry_forward_days=5
        assert prev["total_carry_forward"] == 34.0

        schema = write_client.get("/api/v1/openapi.json").json()
        params = schema["paths"]["/api/annual-close/preview"]["get"]["parameters"]
        cap = next(p for p in params if p["name"] == "max_carry_forward_days")
        assert cap.get("deprecated") is True
