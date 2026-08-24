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


def test_soll_entry_allowed_on_absence_day(planer_client, write_db_path):
    """REGRESSION (Konflikt-Semantik): die Soll-Ebene nimmt an der
    Ist-Konfliktprüfung nicht teil — ein Sollplan-Ziel ist auch auf einem
    Tag mit Abwesenheit zulässig; nur der Istplan-Eintrag bleibt blockiert."""
    emp, shift = _emp_shift(write_db_path)
    date = "2099-06-08"  # Montag
    leave_types = planer_client.get("/api/leave-types").json()
    assert leave_types, "Testaufbau: keine Abwesenheitsarten in der Fixture-DB"
    r_abs = planer_client.post(
        "/api/absences",
        json={"employee_id": emp, "date": date,
              "leave_type_id": leave_types[0]["ID"]},
    )
    assert r_abs.status_code == 200, r_abs.text

    r_ist = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift},
    )
    assert r_ist.status_code == 409
    assert r_ist.json()["detail"]["type"] == "absence_conflict"

    r_soll = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift,
              "schedule_type": 1},
    )
    assert r_soll.status_code in (200, 201), r_soll.text


def test_soll_entry_allowed_over_special_shift(planer_client, write_db_path):
    """REGRESSION (Konflikt-Semantik): Sonderdienste (5SPSHI) sind Ist-Ebene —
    ein zeitgleiches Sollplan-Ziel kollidiert nicht; der Istplan-Eintrag
    bleibt am Overlap-Guard hängen."""
    import pytest
    from sp5lib.database import SP5Database

    from sp5api.routers.schedule import _shift_time_windows

    db = SP5Database(write_db_path)
    emp = db.get_employees()[0]["ID"]
    # Schicht mit Zeitfenster am Montag (Tagindex 0) suchen
    shift = next((s for s in db.get_shifts() if _shift_time_windows(s, 0)), None)
    if shift is None:
        pytest.skip("Keine Schicht mit Montags-Zeitfenster in der Fixture-DB")
    date = "2099-06-01"  # Montag
    r_sp = planer_client.post(
        "/api/einsatzplan",
        json={"employee_id": emp, "date": date, "name": "Sonderdienst",
              "shortname": "SD", "startend": "00:01-23:59"},
    )
    assert r_sp.status_code == 200, r_sp.text

    r_ist = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift["ID"]},
    )
    assert r_ist.status_code == 409
    assert r_ist.json()["detail"]["type"] == "overlapping_shift"

    r_soll = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": date, "shift_id": shift["ID"],
              "schedule_type": 1},
    )
    assert r_soll.status_code in (200, 201), r_soll.text
