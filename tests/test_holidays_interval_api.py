"""V-12 Halbe Feiertage über die API (Spec 3.2.1 Nr. 3/4, Dialog 5.16).

POST/PUT /api/holidays validieren INTERVAL 0/1/2 (0=ganztägig, 1/2=halber
Feiertag; UNSICHER: Zuordnung 1=Vormittag/2=Nachmittag datenseitig
unbestätigt). repeat_years=true legt den Termin zusätzlich für die
nächsten 9 Jahre an.
"""


class TestHolidayInterval:
    def test_create_half_holiday(self, write_client):
        resp = write_client.post(
            "/api/holidays",
            json={"DATE": "2027-12-24", "NAME": "Heiligabend", "INTERVAL": 2},
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["INTERVAL"] == 2

    def test_create_invalid_interval_rejected(self, write_client):
        resp = write_client.post(
            "/api/holidays",
            json={"DATE": "2027-12-31", "NAME": "Silvester", "INTERVAL": 3},
        )
        assert resp.status_code == 422

    def test_update_interval(self, write_client):
        rec = write_client.post(
            "/api/holidays",
            json={"DATE": "2027-12-31", "NAME": "Silvester", "INTERVAL": 0},
        ).json()["record"]
        resp = write_client.put(
            f"/api/holidays/{rec['id']}", json={"INTERVAL": 1}
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["INTERVAL"] == 1
        resp = write_client.put(
            f"/api/holidays/{rec['id']}", json={"INTERVAL": 9}
        )
        assert resp.status_code == 422


class TestHolidayRepeatYears:
    def test_repeat_years_creates_nine_more(self, write_client):
        resp = write_client.post(
            "/api/holidays",
            json={
                "DATE": "2030-05-01",
                "NAME": "Tag der Arbeit X",
                "INTERVAL": 0,
                "repeat_years": True,
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert len(rec["repeated_ids"]) == 9
        dates = set()
        for year in range(2030, 2040):
            rows = write_client.get(f"/api/holidays?year={year}").json()
            dates |= {
                h["DATE"] for h in rows if h.get("NAME") == "Tag der Arbeit X"
            }
        assert dates == {f"{y}-05-01" for y in range(2030, 2040)}

    def test_default_no_repeat(self, write_client):
        resp = write_client.post(
            "/api/holidays",
            json={"DATE": "2031-10-03", "NAME": "Einheit X", "INTERVAL": 0},
        )
        assert resp.status_code == 200
        assert "repeated_ids" not in resp.json()["record"]
