"""Tauschbörse: Ein Tausch braucht einen tauschbaren Dienst BEIDER Mitarbeiter.

Ohne diese Prüfung wurde ein einseitiger Antrag angelegt und die Genehmigung
löschte den vorhandenen Dienst ersatzlos (Kreuz-Tausch schreibt die leere
Gegenseite zurück) — aus dem „Tausch" wurde eine stille Schenkung.
"""

from starlette.testclient import TestClient


def _two_employees(client: TestClient):
    emps = client.get("/api/employees").json()
    return emps[0]["ID"], emps[1]["ID"]


class TestSwapDutyValidation:
    def test_create_rejected_without_any_duty(self, planer_client: TestClient):
        emp1, emp2 = _two_employees(planer_client)
        res = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-01-07",
                "partner_id": emp2,
                "partner_date": "2031-01-08",
            },
        )
        assert res.status_code == 400
        assert "keinen Dienst" in res.json()["detail"]

    def test_create_rejected_when_partner_has_no_duty(self, planer_client: TestClient, ensure_duty):
        emp1, emp2 = _two_employees(planer_client)
        ensure_duty(emp1, "2031-02-03")  # nur der Antragsteller hat einen Dienst
        res = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-02-03",
                "partner_id": emp2,
                "partner_date": "2031-02-04",
            },
        )
        assert res.status_code == 400
        assert "keinen Dienst" in res.json()["detail"]

    def test_create_ok_when_both_have_duty(self, planer_client: TestClient, ensure_duty):
        emp1, emp2 = _two_employees(planer_client)
        ensure_duty(emp1, "2031-03-03")
        ensure_duty(emp2, "2031-03-04")
        res = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-03-03",
                "partner_id": emp2,
                "partner_date": "2031-03-04",
            },
        )
        assert res.status_code == 200

    def test_approve_conflicts_when_duty_removed_after_creation(
        self, planer_client: TestClient, ensure_duty, write_db_path
    ):
        """Wird ein Dienst NACH Antragstellung entfernt, verweigert die
        Genehmigung mit 409 und der Antrag bleibt pending — statt den
        verbliebenen Dienst einseitig zu löschen."""
        emp1, emp2 = _two_employees(planer_client)
        ensure_duty(emp1, "2031-04-07")
        ensure_duty(emp2, "2031-04-08")
        created = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-04-07",
                "partner_id": emp2,
                "partner_date": "2031-04-08",
            },
        )
        assert created.status_code == 200
        swap_id = created.json()["id"]

        # Partner-Dienst verschwindet zwischenzeitlich
        assert planer_client.delete(f"/api/schedule/{emp2}/2031-04-08").status_code == 200

        res = planer_client.patch(
            f"/api/swap-requests/{swap_id}/resolve",
            json={"action": "approve", "resolved_by": "planer"},
        )
        assert res.status_code == 409
        assert "keinen Dienst" in res.json()["detail"]

        # Antrag ist unangetastet pending; der Dienst des Antragstellers existiert noch
        pending = planer_client.get("/api/swap-requests", params={"status": "pending"}).json()
        assert any(e["id"] == swap_id for e in pending)
        from sp5lib.dbf_reader import get_table_fields
        from sp5lib.dbf_writer import find_all_records

        mashi = f"{write_db_path}/5MASHI.DBF"
        remaining = find_all_records(
            mashi, get_table_fields(mashi), EMPLOYEEID=emp1, DATE="2031-04-07"
        )
        assert remaining, "Dienst des Antragstellers darf nicht verloren gehen"

    def test_create_rejected_on_target_date_collision(
        self, planer_client: TestClient, ensure_duty
    ):
        """REGRESSION (Kreuz-Tausch-Datenverlust): hat der Anfragende am Datum
        des Partners BEREITS einen Dienst, würde die Ausführung erst beide
        Originale löschen und dann am Duplikat-Guard scheitern — der
        getauschte Dienst ginge ersatzlos verloren. Anlegen ⇒ 400."""
        emp1, emp2 = _two_employees(planer_client)
        ensure_duty(emp1, "2031-06-02")
        ensure_duty(emp2, "2031-06-03")
        ensure_duty(emp1, "2031-06-03")  # Kollision: A ist am Ziel-Datum belegt
        res = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-06-02",
                "partner_id": emp2,
                "partner_date": "2031-06-03",
            },
        )
        assert res.status_code == 400
        assert "bereits einen Dienst" in res.json()["detail"]

    def test_approve_conflicts_on_target_collision_and_loses_nothing(
        self, planer_client: TestClient, ensure_duty, write_db_path
    ):
        """Entsteht die Ziel-Datum-Kollision NACH der Antragstellung, verweigert
        die Genehmigung mit 409 und BEIDE Original-Dienste bleiben erhalten."""
        emp1, emp2 = _two_employees(planer_client)
        ensure_duty(emp1, "2031-07-01")
        ensure_duty(emp2, "2031-07-02")
        created = planer_client.post(
            "/api/swap-requests",
            json={
                "requester_id": emp1,
                "requester_date": "2031-07-01",
                "partner_id": emp2,
                "partner_date": "2031-07-02",
            },
        )
        assert created.status_code == 200
        swap_id = created.json()["id"]

        ensure_duty(emp1, "2031-07-02")  # Kollision entsteht nachträglich

        res = planer_client.patch(
            f"/api/swap-requests/{swap_id}/resolve",
            json={"action": "approve", "resolved_by": "planer"},
        )
        assert res.status_code == 409
        assert "bereits einen Dienst" in res.json()["detail"]

        # NICHTS wurde gelöscht — beide Originale existieren weiter
        from sp5lib.dbf_reader import get_table_fields
        from sp5lib.dbf_writer import find_all_records

        mashi = f"{write_db_path}/5MASHI.DBF"
        fields = get_table_fields(mashi)
        assert find_all_records(mashi, fields, EMPLOYEEID=emp1, DATE="2031-07-01"), \
            "Dienst des Antragstellers verloren"
        assert find_all_records(mashi, fields, EMPLOYEEID=emp2, DATE="2031-07-02"), \
            "Dienst des Partners verloren"

    def test_approve_executes_cross_date_swap(
        self, planer_client: TestClient, write_db_path
    ):
        """Happy-Path-Lücke: die anderen Tests prüfen nur die 409-Guards und
        `status==approved`, NICHT den eigentlichen Tausch-Effekt. Nach Genehmigung
        eines Kreuz-Tauschs muss A's Dienst bei B (req_date) und B's Dienst bei A
        (par_date) liegen — und die beiden Ausgangsfelder leer sein (misc.py 1041-1047)."""
        import pytest
        from sp5lib.database import SP5Database
        from sp5lib.dbf_reader import get_table_fields
        from sp5lib.dbf_writer import find_all_records

        emp1, emp2 = _two_employees(planer_client)
        db = SP5Database(write_db_path)
        shifts = db.get_shifts()
        if len(shifts) < 2:
            pytest.skip("need two distinct shifts")
        s1, s2 = shifts[0]["ID"], shifts[1]["ID"]
        req_date, par_date = "2031-08-11", "2031-08-12"
        db.add_schedule_entry(emp1, req_date, s1)  # A arbeitet req_date / Schicht s1
        db.add_schedule_entry(emp2, par_date, s2)  # B arbeitet par_date / Schicht s2

        created = planer_client.post("/api/swap-requests", json={
            "requester_id": emp1, "requester_date": req_date,
            "partner_id": emp2, "partner_date": par_date,
        })
        assert created.status_code == 200
        swap_id = created.json()["id"]

        res = planer_client.patch(
            f"/api/swap-requests/{swap_id}/resolve",
            json={"action": "approve", "resolved_by": "planer"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"

        # Tausch ausgeführt: s1 liegt jetzt bei B auf req_date, s2 bei A auf par_date;
        # die Ausgangsfelder sind leer (kein einseitiger Verlust, kein Duplikat).
        mashi = f"{write_db_path}/5MASHI.DBF"
        fields = get_table_fields(mashi)

        def shift_ids_at(emp, date):
            return sorted(
                r.get("SHIFTID")
                for _, r in find_all_records(mashi, fields, EMPLOYEEID=emp, DATE=date)
            )

        assert shift_ids_at(emp2, req_date) == [s1], "A's Dienst muss auf B (req_date) liegen"
        assert shift_ids_at(emp1, par_date) == [s2], "B's Dienst muss auf A (par_date) liegen"
        assert shift_ids_at(emp1, req_date) == [], "A's Ausgangsfeld muss leer sein"
        assert shift_ids_at(emp2, par_date) == [], "B's Ausgangsfeld muss leer sein"

    def test_self_swap_rejected_without_partner_duty(self, app, write_db_path, ensure_duty):
        """Self-Service-Antrag: gleicher Guard wie die Planer-Route."""
        import secrets

        from sp5lib.database import SP5Database

        from sp5api.main import _sessions

        db = SP5Database(write_db_path)
        emps = db.get_employees()
        emp_a, emp_b = emps[0], emps[1]
        ensure_duty(emp_a["ID"], "2031-05-05")  # nur der Anfragende hat einen Dienst

        tok = secrets.token_hex(20)
        _sessions[tok] = {
            "ID": 901,
            "NAME": emp_a.get("NAME", "emp0"),
            "role": "Leser",
            "ADMIN": False,
            "RIGHTS": 1,
        }
        try:
            client = TestClient(app, raise_server_exceptions=False)
            client.headers["X-Auth-Token"] = tok
            res = client.post(
                "/api/self/swap-requests",
                json={
                    "partner_id": emp_b["ID"],
                    "requester_date": "2031-05-05",
                    "partner_date": "2031-05-06",
                },
            )
            assert res.status_code == 400
            assert "keinen Dienst" in res.json()["detail"]
        finally:
            _sessions.pop(tok, None)
