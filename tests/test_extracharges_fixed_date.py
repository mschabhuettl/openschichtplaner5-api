"""Zuschlagsart „festes Datum" (VALIDITY=1, Spec 3.8.2 Nr. 5) über die API."""

from starlette.testclient import TestClient


class TestFixedDateExtracharge:
    def test_create_requires_valid_date(self, admin_client: TestClient):
        res = admin_client.post("/api/extracharges", json={
            "NAME": "Fix ohne Datum", "VALIDITY": 1,
        })
        assert res.status_code == 422
        assert "festes Datum" in res.text

    def test_create_roundtrip_and_update(self, admin_client: TestClient):
        res = admin_client.post("/api/extracharges", json={
            "NAME": "Heiligabend-Zuschlag", "START": 0, "END": 1440,
            "VALIDITY": 1, "DATE": "2026-12-24",
        })
        assert res.status_code == 200, res.text
        xc = res.json().get("record") or res.json()
        xc_id = xc["ID"]
        listing = admin_client.get("/api/extracharges?include_hidden=true").json()
        stored = next(x for x in listing if x["ID"] == xc_id)
        assert stored["VALIDITY"] == 1
        assert stored["DATE"] == "2026-12-24"

        res2 = admin_client.put(f"/api/extracharges/{xc_id}", json={"DATE": "2026-12-31"})
        assert res2.status_code == 200
        listing2 = admin_client.get("/api/extracharges?include_hidden=true").json()
        assert next(x for x in listing2 if x["ID"] == xc_id)["DATE"] == "2026-12-31"

        admin_client.delete(f"/api/extracharges/{xc_id}")

    def test_weekday_mode_unchanged(self, admin_client: TestClient):
        res = admin_client.post("/api/extracharges", json={
            "NAME": "Nachtzuschlag Wochentage", "START": 1320, "END": 360,
            "VALIDITY": 0, "VALIDDAYS": "1111100",
        })
        assert res.status_code == 200, res.text
        xc = res.json().get("record") or res.json()
        admin_client.delete(f"/api/extracharges/{xc['ID']}")
