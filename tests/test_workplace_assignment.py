"""A5: Arbeitsplatz-Zuordnung im Dienstplan (Spec 6.4) über die API."""


def _emp_shift_wp(write_db_path):
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    wps = db.get_workplaces()
    return db.get_employees()[0]["ID"], db.get_shifts()[0]["ID"], wps[0]["ID"]


def test_create_entry_with_workplace(planer_client, write_db_path):
    emp, shift, wp = _emp_shift_wp(write_db_path)
    r = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": "2099-07-06", "shift_id": shift,
              "workplace_id": wp},
    )
    assert r.status_code in (200, 201), r.text
    sched = planer_client.get("/api/schedule?year=2099&month=7").json()
    e = next(x for x in sched if x["employee_id"] == emp and x["date"] == "2099-07-06")
    assert e["workplace_id"] == wp
    assert e.get("workplace_name")


def test_assign_workplace_to_existing(planer_client, write_db_path):
    emp, shift, wp = _emp_shift_wp(write_db_path)
    planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": "2099-07-07", "shift_id": shift},
    )
    r = planer_client.post(
        "/api/schedule/workplace",
        json={"employee_id": emp, "date": "2099-07-07", "workplace_id": wp},
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    sched = planer_client.get("/api/schedule?year=2099&month=7").json()
    e = next(x for x in sched if x["employee_id"] == emp and x["date"] == "2099-07-07")
    assert e["workplace_id"] == wp


def test_assign_workplace_unknown_workplace(planer_client, write_db_path):
    emp, shift, _ = _emp_shift_wp(write_db_path)
    planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "date": "2099-07-08", "shift_id": shift},
    )
    r = planer_client.post(
        "/api/schedule/workplace",
        json={"employee_id": emp, "date": "2099-07-08", "workplace_id": 99999},
    )
    assert r.status_code == 404


def test_assign_workplace_no_entry(planer_client, write_db_path):
    emp, _, wp = _emp_shift_wp(write_db_path)
    r = planer_client.post(
        "/api/schedule/workplace",
        json={"employee_id": emp, "date": "2099-07-28", "workplace_id": wp},
    )
    assert r.status_code == 404
