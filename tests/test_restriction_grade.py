"""A4: Einschränkungs-Grad (5RESTR.RESTRICT, Spec 4.11, Dekompilat-belegt).

0=keine, 1=„auf Anfrage" (weich → 200 + Warnung), 2=„nie" (hart → 409).
"""


def _emp_shift(db_path):
    from sp5lib.database import SP5Database

    db = SP5Database(db_path)
    return db.get_employees()[0]["ID"], db.get_shifts()[0]["ID"]


def _restrict(db_path, emp, shift, grade, weekday=0):
    from sp5lib.database import SP5Database

    SP5Database(db_path).set_restriction(
        employee_id=emp, shift_id=shift, reason="Test", weekday=weekday, grade=grade
    )


def test_grade2_never_blocks_hard(planer_client, write_db_path):
    emp, shift = _emp_shift(write_db_path)
    _restrict(write_db_path, emp, shift, grade=2, weekday=0)  # Montag
    r = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "shift_id": shift, "date": "2099-01-05"},  # Mo
    )
    assert r.status_code == 409
    assert "restriction" in r.json().get("detail", "").lower()


def test_grade1_onrequest_allows_with_warning(planer_client, write_db_path):
    emp, shift = _emp_shift(write_db_path)
    _restrict(write_db_path, emp, shift, grade=1, weekday=0)
    r = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "shift_id": shift, "date": "2099-01-05"},
    )
    assert r.status_code in (200, 201), r.text
    assert "warning" in r.json()
    assert "anfrage" in r.json()["warning"].lower()


def test_grade0_none_no_restriction(planer_client, write_db_path):
    emp, shift = _emp_shift(write_db_path)
    _restrict(write_db_path, emp, shift, grade=0, weekday=0)
    r = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "shift_id": shift, "date": "2099-01-05"},
    )
    assert r.status_code in (200, 201), r.text
    assert "warning" not in r.json()


def test_set_restriction_updates_grade(planer_client, admin_client, write_db_path):
    emp, shift = _emp_shift(write_db_path)
    # erst „nie", dann auf „auf Anfrage" herabsetzen
    r1 = admin_client.post(
        "/api/restrictions",
        json={"employee_id": emp, "shift_id": shift, "weekday": 0, "grade": 2},
    )
    assert r1.status_code == 200, r1.text
    r2 = admin_client.post(
        "/api/restrictions",
        json={"employee_id": emp, "shift_id": shift, "weekday": 0, "grade": 1},
    )
    assert r2.status_code == 200, r2.text
    # jetzt nur noch Warnung statt 409
    r3 = planer_client.post(
        "/api/schedule",
        json={"employee_id": emp, "shift_id": shift, "date": "2099-01-05"},
    )
    assert r3.status_code in (200, 201) and "warning" in r3.json()
