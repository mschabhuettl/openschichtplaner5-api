"""V-3 Teiltags-Abwesenheiten über die API (Spec 3.5.2/D-54, R6.6-1..4).

POST/PUT /api/absences nehmen interval/start_time/end_time entgegen
(0=ganz, 1=vormittags, 2=nachmittags, 3=stundenweise) und liefern die
Felder in der Response; die Liste enthält sie ebenfalls.
"""


def _first_ids(client):
    emp_id = client.get("/api/employees").json()[0]["ID"]
    lt_id = client.get("/api/leave-types").json()[0]["ID"]
    return emp_id, lt_id


class TestCreateAbsenceInterval:
    def test_create_half_day(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        resp = write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-03-01",
                "leave_type_id": lt_id,
                "interval": 1,
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert rec["INTERVAL"] == 1
        assert (rec["START"], rec["END"]) == (0, 0)
        # Liste liefert die Teiltags-Felder mit
        rows = write_client.get(
            f"/api/absences?employee_id={emp_id}&year=2027"
        ).json()
        row = next(r for r in rows if r["date"] == "2027-03-01")
        assert row["interval"] == 1

    def test_create_hourly_with_times(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        resp = write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-03-02",
                "leave_type_id": lt_id,
                "interval": 3,
                "start_time": 480,
                "end_time": 720,
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert (rec["INTERVAL"], rec["START"], rec["END"]) == (3, 480, 720)

    def test_create_hourly_day_wrap_allowed(self, write_client):
        """END < START = rechnerischer Tageswechsel (Spec 3.5.2 Nr. 3)."""
        emp_id, lt_id = _first_ids(write_client)
        resp = write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-03-03",
                "leave_type_id": lt_id,
                "interval": 3,
                "start_time": 1320,
                "end_time": 120,
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert (rec["START"], rec["END"]) == (1320, 120)

    def test_create_hourly_equal_times_rejected(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        resp = write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-03-04",
                "leave_type_id": lt_id,
                "interval": 3,
                "start_time": 480,
                "end_time": 480,
            },
        )
        assert resp.status_code == 422

    def test_create_invalid_interval_rejected(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        resp = write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-03-05",
                "leave_type_id": lt_id,
                "interval": 5,
            },
        )
        assert resp.status_code == 422


class TestUpdateAbsenceInterval:
    def test_update_interval(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-04-01",
                "leave_type_id": lt_id,
            },
        )
        resp = write_client.put(
            f"/api/absences/{emp_id}/2027-04-01",
            json={"interval": 2},
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["INTERVAL"] == 2
        # stundenweise mit Zeitfenster
        resp = write_client.put(
            f"/api/absences/{emp_id}/2027-04-01",
            json={"interval": 3, "start_time": 600, "end_time": 840},
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert (rec["INTERVAL"], rec["START"], rec["END"]) == (3, 600, 840)

    def test_update_missing_absence_404(self, write_client):
        emp_id, _ = _first_ids(write_client)
        resp = write_client.put(
            f"/api/absences/{emp_id}/2027-04-30",
            json={"interval": 1},
        )
        assert resp.status_code == 404

    def test_update_bad_date_400(self, write_client):
        resp = write_client.put(
            "/api/absences/1/not-a-date",
            json={"interval": 1},
        )
        assert resp.status_code == 400

    def test_update_unknown_leave_type_404(self, write_client):
        emp_id, lt_id = _first_ids(write_client)
        write_client.post(
            "/api/absences",
            json={
                "employee_id": emp_id,
                "date": "2027-04-02",
                "leave_type_id": lt_id,
            },
        )
        resp = write_client.put(
            f"/api/absences/{emp_id}/2027-04-02",
            json={"leave_type_id": 999999},
        )
        assert resp.status_code == 404
