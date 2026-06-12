"""A1: Soll-/Istplan-Sicht über /api/schedule (Spec 4.12).

5MASHI.TYPE 0=Istplan, 1=Sollplan (Dekompilat-belegt). Der plan-Query wählt
die Sicht; POST /api/schedule schreibt schedule_type.
"""


def _emp_shift(write_db_path):
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    return db.get_employees()[0]["ID"], db.get_shifts()[0]["ID"]


def test_plan_filter_separates_soll_and_ist(planer_client, write_db_path):
    emp, shift = _emp_shift(write_db_path)
    date = "2099-05-04"
    # Istplan-Eintrag (Default) + Sollplan-Eintrag am selben Tag
    r1 = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift},
    )
    assert r1.status_code in (200, 201), r1.text
    r2 = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift,
              "schedule_type": 1},
    )
    assert r2.status_code in (200, 201), r2.text

    def at(plan):
        resp = planer_client.get(f"/api/schedule?year=2099&month=5&plan={plan}")
        assert resp.status_code == 200, resp.text
        return [e for e in resp.json()
                if e["date"] == date and e["kind"] == "shift"]

    ist = at("ist")
    soll = at("soll")
    both = at("both")
    assert [e["schedule_type"] for e in ist] == [0]
    assert [e["schedule_type"] for e in soll] == [1]
    assert sorted(e["schedule_type"] for e in both) == [0, 1]
    # Default (ohne plan) = Istplan
    default = planer_client.get("/api/schedule?year=2099&month=5")
    assert [e["schedule_type"] for e in default.json()
            if e["date"] == date and e["kind"] == "shift"] == [0]


def test_invalid_plan_rejected(planer_client, write_db_path):
    r = planer_client.get("/api/schedule?year=2099&month=5&plan=quatsch")
    assert r.status_code == 400
